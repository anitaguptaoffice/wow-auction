#!/usr/bin/env python3
"""Data-only blue/green switch and rollback for the existing single-host API.

Uses the previous container's immutable image ID: it cannot deploy UI/API changes.
No public write endpoint is introduced. Run this command on the host via SSH.
Compatible with the host's Python 3.6 standard library.
"""
import argparse
import hashlib
import json
import os
import re
from types import SimpleNamespace
from pathlib import Path
import subprocess
import time
import urllib.parse
import urllib.request
import uuid


def command(*args):
    return subprocess.check_output(list(args), stderr=subprocess.STDOUT, universal_newlines=True).strip()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def atomic_write(path, content, mode=0o600):
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('xb') as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(str(temporary), mode)
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


def save_record(path, record):
    atomic_write(path, (json.dumps(record, ensure_ascii=False, indent=2) + '\n').encode('utf-8'))


def api_json(base, path):
    request = urllib.request.Request(base + path, headers={'Accept': 'application/json', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode('utf-8'))


def verify_catalog(catalog, scans):
    expected = {}
    for scan in scans:
        for regional in (True, False):
            identity = (scan['region_id'], 0 if regional else scan['realm_id'])
            expected.setdefault(identity, set()).add(scan['scan_id'] * 2 + (0 if regional else 1))
    actual = {(row['regionID'], row['realmID']): {s['scanId'] for s in row['scans']} for row in catalog['realms']}
    if actual != expected:
        raise ValueError('Candidate catalog does not exactly match the staged snapshot selection')
    entries = {(r['regionID'], r['realmID'], s['scanId']): s for r in catalog['realms'] for s in r['scans']}
    for source in scans:
        observations = [entries[source['region_id'], realm, source['scan_id'] * 2 + offset]
                        for realm, offset in ((0, 0), (source['realm_id'], 1))]
        if 'timestamp' in source and any(s.get('scannedAtUnix') != source['timestamp'] for s in observations):
            raise ValueError('Catalog snapshot timestamps differ from the verified report')
        if 'record_count' in source and sum(s.get('listingCount', -1) for s in observations) != source['record_count']:
            raise ValueError('Catalog snapshot row counts differ from the verified report')


def verify_market_api(base, scans, catalog=None):
    """Data-driven verification for every market; no realm IDs or fixture items."""
    catalog = catalog or api_json(base, '/api/market/catalog')
    verify_catalog(catalog, scans)
    checked = []
    for realm in catalog['realms']:
        sid = realm['latestScanId']
        observation = next(s for s in realm['scans'] if s['scanId'] == sid)
        def query(path, **values):
            return api_json(base, path + '?' + urllib.parse.urlencode(dict(scan_id=sid, **values)))
        status = query('/api/market/status')
        if (status.get('realmID') != realm['realmID'] or status.get('regionID') != realm['regionID']
                or status.get('scannedAt') != observation['scannedAt']):
            raise ValueError('Market status identity or timestamp mismatch')
        result = query('/api/market/items', page_size=1)
        if result.get('scanId') != sid or len(result['items']) != min(1, result['total']):
            raise ValueError('Market listing page mismatch')
        if result['items']:
            item = result['items'][0]
            if (item['scanId'], item['regionID'], item['realmID'], item['scannedAt']) != (
                    sid, realm['regionID'], realm['realmID'], observation['scannedAt']):
                raise ValueError('Market result belongs to a different observation')
            detail = query('/api/market/listings', market_key=item['marketKey'], page_size=25)
            if detail['total'] != item['listingCount'] or len(detail['items']) != min(25, item['listingCount']):
                raise ValueError('Detail pagination count mismatch')
            if detail['total'] > 25:
                second = query('/api/market/listings', market_key=item['marketKey'], page_size=25, page=2)
                if second['total'] != detail['total'] or len(second['items']) != min(25, detail['total'] - 25):
                    raise ValueError('Second detail page mismatch')
            history = query('/api/market/history/view', market_key=item['marketKey'], max_points=40)
            if (not history['points'] or history['points'][-1]['scanId'] != sid
                    or any(p['realmID'] != realm['realmID'] or p['regionID'] != realm['regionID'] for p in history['points'])):
                raise ValueError('History observation identity mismatch')
        checked.append(dict(realm=realm['realm'], region_id=realm['regionID'], realm_id=realm['realmID'],
                            scan_id=sid, scanned_at=observation['scannedAt'], market_items=result['total']))
    return dict(market_count=len(checked), realm_count=sum(r['realm_id'] != 0 for r in checked),
                source_snapshot_count=len(scans), source_record_count=sum(s.get('record_count', 0) for s in scans),
                markets=checked, checks='catalog identities, all snapshot timestamps/counts, per-market status, items, detail pagination and history')


def wait_ready(base, scans, timeout=90):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            health = api_json(base, '/health')
            if health.get('status') != 'ok':
                raise ValueError('Candidate health is not ok')
            return verify_market_api(base, scans)
        except Exception as exc:
            error = exc
            time.sleep(1)
    raise RuntimeError('Candidate did not become ready: ' + str(error))


def replace_upstream(config, old_port, new_port):
    old = ('proxy_pass http://127.0.0.1:%d;' % old_port).encode('ascii')
    new = ('proxy_pass http://127.0.0.1:%d;' % new_port).encode('ascii')
    if config.count(old) != 1 or old_port == new_port:
        raise ValueError('Expected exactly one matching current API upstream')
    return config.replace(old, new)


def wait_matching_catalog(base, scans=None, expected_catalog=None):
    deadline = time.monotonic() + 20
    while True:
        try:
            catalog = api_json(base, '/api/market/catalog')
            if expected_catalog is not None:
                if catalog != expected_catalog:
                    raise ValueError('Public catalog does not match rollback state')
            else:
                verify_catalog(catalog, scans)
                return verify_market_api(base, scans, catalog)
            return
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def switch(args):
    database = Path(args.database).resolve()
    config = Path(args.config).resolve()
    report = json.loads(Path(args.report).read_text(encoding='utf-8'))
    if sha256(database) != report['database_sha256']:
        raise ValueError('Database does not match its verified report')
    if len(report['scans']) != args.expect_scans or not all(c['all_modeled_fields_match'] for c in report['roundtrip_checks']):
        raise ValueError('Snapshot count or verification report mismatch')
    assets = []
    if getattr(args, 'runtime_dir', None):
        assets += ['-v', str(args.runtime_dir) + ':/data/runtime:ro',
                   '-e', 'COMPACT_UPDATE_STATUS_FILE=/data/runtime/status.json']
    if report.get('icon_manifest_sha256'):
        manifest = database.parent / 'icon-map.json'
        if sha256(manifest) != report['icon_manifest_sha256']:
            raise ValueError('Icon manifest checksum mismatch')
        assets += ['-v', str(manifest) + ':/data/icon-map.json:ro']
        icons = database.parent / 'wow-icons'
        if icons.is_dir():
            assets += ['-v', str(icons) + ':/data/wow-icons:ro']
    if command('docker', 'inspect', '--format', '{{.State.Running}}', args.previous_container) != 'true':
        raise ValueError('Previous API container must be running')
    mounts = json.loads(command('docker', 'inspect', '--format', '{{json .Mounts}}', args.previous_container))
    previous_db = next(m['Source'] for m in mounts if m['Destination'] == '/data/market.duckdb')
    if sha256(previous_db) != args.expected_current_sha:
        raise ValueError('Active database changed; rebuild/review the data selection before switching')
    image = command('docker', 'inspect', '--format', '{{.Image}}', args.previous_container)
    previous_catalog = api_json('http://127.0.0.1:%d' % args.previous_port, '/api/market/catalog')
    original = config.read_bytes()
    updated = replace_upstream(original, args.previous_port, args.port)
    backup = Path(args.backup_dir).resolve()
    backup.mkdir(parents=True, exist_ok=True)
    name = time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8]
    record_path = backup / (name + '.json')
    config_backup = backup / (name + '.nginx.conf')
    atomic_write(config_backup, original)
    record = dict(status='preparing', config=str(config), config_backup=str(config_backup),
                  previous_config_sha=hashlib.sha256(original).hexdigest(),
                  next_config_sha=hashlib.sha256(updated).hexdigest(),
                  previous_container=args.previous_container, container=args.container,
                  previous_database=previous_db, database=str(database), image=image,
                  database_sha256=report['database_sha256'], previous_database_sha256=args.expected_current_sha,
                  port=args.port, previous_port=args.previous_port, scans=report['scans'],
                  previous_catalog=previous_catalog, public_url=args.public_url.rstrip('/'))
    save_record(record_path, record)
    candidate_started = False
    config_changed = False
    try:
        command('docker', 'run', '-d', '--name', args.container, '--restart', 'unless-stopped',
                '--memory', '2g', '--memory-swap', '2g', '--cpus', '2', '--pids-limit', '128',
                '--read-only', '--tmpfs', '/tmp:rw,nosuid,size=128m', '--cap-drop', 'ALL',
                '--security-opt', 'no-new-privileges', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3',
                '-p', '127.0.0.1:%d:8000' % args.port, '-v', str(database) + ':/data/market.duckdb:ro', *(assets + [image]))
        candidate_started = True
        wait_ready('http://127.0.0.1:%d' % args.port, report['scans'])
        if config.read_bytes() != original:
            raise ValueError('Nginx config changed during candidate startup')
        atomic_write(config, updated, config.stat().st_mode & 0o777)
        config_changed = True
        command('nginx', '-t')
        command('nginx', '-s', 'reload')
        # Verify through the actual public Nginx route, not just the new container.
        verification = wait_matching_catalog(args.public_url.rstrip('/'), scans=report['scans'])
        if isinstance(verification, dict):
            record['verification'] = verification
        record['status'] = 'active'
        save_record(record_path, record)
    except Exception as exc:
        if config_changed:
            atomic_write(config, original, config.stat().st_mode & 0o777)
            command('nginx', '-t')
            command('nginx', '-s', 'reload')
        if candidate_started:
            command('docker', 'stop', args.container)
        record.update(status='failed_and_reverted', error=str(exc))
        save_record(record_path, record)
        raise
    # Failure to stop a healthy old container is a cleanup issue, never a reason
    # to revert a verified new data route or misreport the activation.
    try:
        command('docker', 'stop', args.previous_container)
        record['previous_stopped'] = True
    except Exception:
        record['previous_stopped'] = False
    save_record(record_path, record)
    result = dict(status='active', rollback_record=str(record_path), database_sha256=report['database_sha256'])
    if record.get('verification'):
        result['verification'] = record['verification']
    return result


def rollback(args):
    record_path = Path(args.record)
    record = json.loads(record_path.read_text(encoding='utf-8'))
    config = Path(record['config'])
    if record.get('status') == 'rolled_back':
        return dict(status='rolled_back', already=True)
    if record.get('status') != 'active' or sha256(config) != record['next_config_sha']:
        raise ValueError('This deployment is no longer the active upstream; refusing to overwrite later changes')
    if sha256(record['previous_database']) != record['previous_database_sha256']:
        raise ValueError('Rollback database checksum mismatch')
    command('docker', 'start', record['previous_container'])
    deadline = time.monotonic() + 90
    while True:
        try:
            if api_json('http://127.0.0.1:%d' % record['previous_port'], '/health').get('status') == 'ok':
                break
        except Exception:
            pass
        if time.monotonic() > deadline:
            raise RuntimeError('Previous container failed health; current route remains unchanged')
        time.sleep(1)
    original = Path(record['config_backup']).read_bytes()
    if hashlib.sha256(original).hexdigest() != record['previous_config_sha']:
        raise ValueError('Rollback configuration checksum mismatch')
    current = config.read_bytes()
    try:
        atomic_write(config, original, config.stat().st_mode & 0o777)
        command('nginx', '-t')
        command('nginx', '-s', 'reload')
        wait_matching_catalog(record['public_url'], expected_catalog=record['previous_catalog'])
    except Exception:
        atomic_write(config, current, config.stat().st_mode & 0o777)
        command('nginx', '-t')
        command('nginx', '-s', 'reload')
        raise
    record['status'] = 'rolled_back'
    save_record(record_path, record)
    command('docker', 'stop', record['container'])
    return dict(status='rolled_back', database=record['previous_database'])


def _activate(args):
    """Discover the current data container; each update alternates the two local ports."""
    database = Path(args.database).resolve()
    report = json.loads(Path(args.report).read_text(encoding='utf-8'))
    receipt = database.parent / 'publication.json'
    upstreams = re.findall(rb'proxy_pass http://127\.0\.0\.1:(\d+);', Path(args.config).read_bytes())
    if len(upstreams) != 1 or int(upstreams[0]) not in (8180, 8181):
        raise ValueError('Expected exactly one API upstream on port 8180 or 8181')
    port = int(upstreams[0])
    containers = command('docker', 'ps', '--format', '{{.Names}}').splitlines()
    current = []
    for name in containers:
        bindings = json.loads(command('docker', 'inspect', '--format', '{{json .NetworkSettings.Ports}}', name))
        if any(b['HostIp'] == '127.0.0.1' and b['HostPort'] == str(port) for b in bindings.get('8000/tcp', []) or []):
            current.append(name)
    if len(current) != 1:
        raise ValueError('Could not identify exactly one active API container')
    mounts = json.loads(command('docker', 'inspect', '--format', '{{json .Mounts}}', current[0]))
    active_db = next(m['Source'] for m in mounts if m['Destination'] == '/data/market.duckdb')
    if receipt.exists():
        result = json.loads(receipt.read_text(encoding='utf-8'))
        if result.get('database_sha256') != sha256(active_db) or sha256(database) != report['database_sha256']:
            raise ValueError('This publication receipt no longer describes the active database')
        return result
    if sha256(active_db) == report['database_sha256']:
        # Recover a successful switch whose final receipt write was interrupted.
        for record_path in sorted(Path(args.backup_dir).glob('*.json'), reverse=True):
            record = json.loads(record_path.read_text(encoding='utf-8'))
            if record.get('status') == 'active' and record.get('database_sha256') == report['database_sha256']:
                result = dict(status='active', rollback_record=str(record_path), database_sha256=report['database_sha256'])
                save_record(receipt, result)
                return result
        raise ValueError('Candidate is active without a recoverable switch record')
    params = SimpleNamespace(database=str(database), report=args.report, config=args.config,
        backup_dir=args.backup_dir, expected_current_sha=args.expected_current_sha,
        public_url=args.public_url, expect_scans=len(report['scans']), previous_container=current[0],
        container='wow-auction-data-' + database.parent.name + '-' + uuid.uuid4().hex[:8],
        previous_port=port, port=8181 if port == 8180 else 8180, runtime_dir=args.runtime_dir)
    result = switch(params)
    save_record(receipt, result)
    return result


def activate(args):
    runtime = Path(getattr(args, 'runtime_dir', None) or Path(args.database).resolve().parent.parent / 'runtime')
    runtime.mkdir(parents=True, exist_ok=True)
    os.chmod(str(runtime), 0o755)
    args.runtime_dir = str(runtime)
    state = dict(phase='publishing', updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    def save_status():
        atomic_write(runtime / 'status.json', (json.dumps(state) + '\n').encode('utf-8'), mode=0o644)
    save_status()
    try:
        result = _activate(args)
    except Exception:
        state.update(phase='failed', updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        save_status()
        raise
    state.update(phase='published', updated_at=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
    save_status()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command')
    deploy = sub.add_parser('switch')
    for name in ('database', 'report', 'previous-container', 'container', 'config', 'backup-dir', 'expected-current-sha', 'public-url'):
        deploy.add_argument('--' + name, required=True)
    deploy.add_argument('--previous-port', type=int, required=True)
    deploy.add_argument('--port', type=int, required=True)
    deploy.add_argument('--expect-scans', type=int, required=True)
    undo = sub.add_parser('rollback')
    undo.add_argument('record')
    update = sub.add_parser('activate')
    for name in ('database', 'report', 'config', 'backup-dir', 'expected-current-sha', 'public-url'):
        update.add_argument('--' + name, required=True)
    args = parser.parse_args()
    if args.command not in ('switch', 'rollback', 'activate'):
        parser.error('Choose switch, activate or rollback')
    # One host-side data switch at a time. This lock is independent of imports.
    lock = Path('/opt/wow-auction/data/sync/dataset-switch.lock')
    from sync_state import Store, guard
    sync_root = Path('/opt/wow-auction/data/sync')
    with guard(sync_root):
        if Store(sync_root).active():
            raise ValueError('A snapshot sync job is active; retry after it finishes')
        fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode('ascii'))
        result = switch(args) if args.command == 'switch' else activate(args) if args.command == 'activate' else rollback(args)
        print(json.dumps(result, ensure_ascii=True, indent=2))
    finally:
        os.close(fd)
        lock.unlink()


if __name__ == '__main__':
    main()
