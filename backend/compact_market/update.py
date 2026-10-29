"""Serialized, resumable client archive -> immutable generation -> acceptance -> publication."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import glob
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid

from app.services.auction_importer import sha256_file
from app.icon_names import safe_icon_name
from compact_market.manage import archive_source, generation_lock, inventory, stage_generation, write_json
from compact_market.unified import UnifiedMarketStore


def now():
    return datetime.now(timezone.utc).isoformat()


def hook(command, context, timeout=1800):
    """Only argv arrays from the operator's local config; never shell interpolation."""
    if not isinstance(command, list) or not command or not all(isinstance(v, str) for v in command):
        raise ValueError('Hook command must be a nonempty argv array')
    args = command[:]
    for index, value in enumerate(args):
        for key, replacement in context.items():
            value = value.replace('{' + key + '}', str(replacement))
        args[index] = value
    return subprocess.run(args, check=True, capture_output=True, text=True, encoding='utf-8', timeout=timeout)


def load_config(path):
    path = Path(path).resolve()
    config = json.loads(path.read_text(encoding='utf-8'))
    for key in ('root', 'seed_database', 'icon_map', 'listfile', 'icon_cache', 'icon_state'):
        if config.get(key):
            config[key] = str((path.parent / config[key]).resolve())
    config['sources'] = [str((path.parent / p).resolve()) for p in config.get('sources', [])]
    for key in ('client_roots', 'archive_roots'):
        config[key] = [str((path.parent / p).resolve()) for p in config.get(key, [])]
    config['config_dir'] = str(path.parent)
    return config


def market_policy(config, scans, current=None):
    mode = config.get('realm_policy', 'allowlist')
    if mode not in ('allowlist', 'discover'):
        raise ValueError('realm_policy must be allowlist or discover')
    known = {f"{s['region_id']}:{s['realm_id']}" for s in scans}
    existing = current or {}
    fallback = existing.get('new_realm_after') if existing.get('realm_policy') == 'discover' else None
    floor = int(config.get('new_realm_after', fallback if fallback is not None else max((s['timestamp'] for s in scans), default=0)))
    if floor < 0:
        raise ValueError('new_realm_after must be a nonnegative Unix timestamp')
    return dict(allowed=None if mode == 'discover' else set(config.get('allowed_realms', known)),
                excluded=set(config.get('excluded_realms', [])), floor=floor)


def verify_public_api(config, scans):
    if not config.get('public_url'):
        raise ValueError('Configure public_url for publication verification')
    path = Path(__file__).resolve().parents[2] / 'deploy' / 'dataset.py'
    spec = importlib.util.spec_from_file_location('compact_dataset_verifier', path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    return verifier.verify_market_api(config['public_url'].rstrip('/'), scans)


def discover_client_sources(config):
    """Header hints select candidates only; the importer still validates complete files."""
    pointer = Path(config['root']) / 'current.json'
    current = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {}
    database = current.get('database', config['seed_database'])
    scans = inventory(Path(database))['scans']
    policy = market_policy(config, scans, current)
    latest = {}
    for scan in scans:
        identity = f"{scan['region_id']}:{scan['realm_id']}"
        latest[identity] = max(latest.get(identity, 0), scan['timestamp'])
    selected = []
    observations = []
    new_realms = {}
    skipped = []
    if not config.get('client_roots') and not config.get('archive_roots'):
        raise ValueError('Configure client_roots or archive_roots before discovery')
    candidates = []
    for root in config.get('client_roots', []):
        if not Path(root).is_dir():
            raise ValueError('Client account directory does not exist: ' + str(root))
        candidates.extend(Path(root).glob('*/*/*/SavedVariables/AuctionSearchExample.lua'))
    for root in config.get('archive_roots', []):
        if not Path(root).is_dir():
            raise ValueError('Archive directory does not exist: ' + str(root))
        candidates.extend(Path(root).glob('auction-*.lua.tgz'))
    for source in sorted(set(candidates)):
            try:
                if source.suffix == '.lua':
                    with source.open(encoding='utf-8-sig') as stream:
                        header = stream.read(131072)
                else:
                    header = ''
                    with tarfile.open(source, 'r|gz') as archive:
                        for member in archive:
                            if member.isfile() and member.name.endswith('.lua'):
                                stream = archive.extractfile(member)
                                with stream:
                                    header = stream.read(131072).decode('utf-8-sig')
                                break
                summary = re.search(r'\["lastScan"\]\s*=\s*\{(.*?)\}', header, re.S)
                stamp = re.search(r'\["lastScanTime"\]\s*=\s*(\d+)', header)
                fields = dict(re.findall(r'\["(regionID|realmID)"\]\s*=\s*(\d+)', summary[1])) if summary else {}
                if not stamp or len(fields) != 2:
                    skipped.append(dict(source=str(source), reason='no_scan_header'))
                    continue
                identity = f"{fields['regionID']}:{fields['realmID']}"
                if identity in policy['excluded']:
                    skipped.append(dict(source=str(source), reason='realm_excluded'))
                elif policy['allowed'] is not None and identity not in policy['allowed']:
                    skipped.append(dict(source=str(source), reason='realm_not_configured'))
                elif int(stamp[1]) <= latest.get(identity, policy['floor']):
                    skipped.append(dict(source=str(source), reason='not_newer_than_current' if identity in latest else 'older_than_new_realm_cutoff'))
                else:
                    selected.append(str(source))
                    name = re.search(r'\["realmName"\]\s*=\s*"([^"\r\n]+)"', summary[1])
                    realm = name[1] if name else identity
                    observations.append(dict(source=str(source), realm=realm, identity=identity,
                                             scanned_at=datetime.fromtimestamp(int(stamp[1]), timezone.utc).isoformat()))
                    if identity not in latest:
                        new_realms[identity] = realm
            except (OSError, UnicodeError, tarfile.TarError) as exc:
                skipped.append(dict(source=str(source), reason='unreadable', error=str(exc)))
    return dict(sources=selected, skipped=skipped, observations=observations,
                new_realms=[dict(identity=k, realm=v) for k, v in sorted(new_realms.items())])


def prepare_icons(database, destination, source_map=None, listfile=None, cache_dirs=()):
    known = json.loads(Path(source_map).read_text(encoding='utf-8')) if source_map else {}
    import duckdb
    with duckdb.connect(str(database), read_only=True) as db:
        textures = {str(v[0]) for v in db.execute('SELECT DISTINCT texture FROM descriptors WHERE texture IS NOT NULL').fetchall()}
    if listfile:
        for line in Path(listfile).read_text(encoding='utf-8-sig').splitlines():
            key, _, value = line.partition(';')
            match = re.fullmatch(r'(?:interface|housing)/icons/([^/]+)\.blp', value.replace('\\', '/').lower())
            if key in textures and match:
                known[key] = re.sub(r'\s', '-', match[1])
    icons = {key: known.get(key, 'filedata-' + key) for key in sorted(textures)}
    if any(not safe_icon_name(value) for value in icons.values()):
        raise ValueError('Icon manifest contains an unsafe name')
    write_json(destination, icons)
    output = destination.parent / 'wow-icons'
    output.mkdir(exist_ok=True)
    for name in set(icons.values()):
        for extension in ('jpg', 'webp'):
            for folder in cache_dirs:
                source = Path(folder) / (name + '.' + extension)
                if source.is_file():
                    target = output / source.name
                    if source.resolve() != target.resolve():
                        shutil.copyfile(source, target)
                    break
    return dict(texture_count=len(icons), unresolved_names=sum(v.startswith('filedata-') for v in icons.values()))


def accept(database, report):
    if sha256_file(database) != report['database_sha256']:
        raise ValueError('Candidate checksum mismatch')
    store = UnifiedMarketStore(database, memory_limit='256MB')
    try:
        expected = {(s['region_id'], r, s['scan_id'] * 2 + offset)
                    for s in report['scans'] for r, offset in ((0, 0), (s['realm_id'], 1))}
        actual = {(r['regionID'], r['realmID'], s['scanId']) for r in store.catalog()['realms'] for s in r['scans']}
        if actual != expected:
            raise ValueError('Candidate market inventory mismatch')
        for realm in store.catalog()['realms']:
            rows = store.items(scan_id=realm['latestScanId'], page_size=1)['items']
            if rows:
                row = rows[0]
                detail = store.listings(scan_id=row['scanId'], market_key=row['marketKey'], page_size=1)
                if detail['total'] != row['listingCount'] or not detail['items']:
                    raise ValueError('Candidate listing acceptance failed')
    finally:
        store.close()


def run_update(config, sources=None, publish=False):
    root = Path(config['root']).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with generation_lock(root / 'update'):
        pointer = root / 'current.json'
        # An interrupted publisher can have switched remotely; do not blindly replay it.
        status_file = root / 'status.json'
        previous = json.loads(status_file.read_text(encoding='utf-8')) if status_file.exists() else {}
        if previous.get('phase') in ('publishing', 'publication_unknown'):
            raise ValueError('Publication outcome is unknown; reconcile the publisher receipt before retrying')
        job_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
        jobs = root / 'jobs'
        jobs.mkdir(exist_ok=True)
        job = dict(id=job_id, phase='discovery', started_at=now())
        def save(phase, **values):
            job.update(phase=phase, updated_at=now(), **values)
            write_json(jobs / (job_id + '.json'), job)
            write_json(status_file, job)
        save('discovery')
        try:
            current = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {
                'database': str(Path(config['seed_database']).resolve()),
                'database_sha256': sha256_file(Path(config['seed_database']))}
            if sha256_file(Path(current['database'])) != current['database_sha256']:
                raise ValueError('Current generation checksum changed')
            job['active_database'] = current['database']
            inputs = [Path(p).resolve() for p in sources] if sources is not None else sorted({
                Path(p).resolve() for pattern in config.get('sources', []) for p in glob.glob(pattern, recursive=True)})
            if not inputs and not (publish and pointer.exists() and not current.get('publication')):
                raise ValueError('No snapshot sources found')
            # Raw hashes are receipts, not a substitute for manifest validation on new files.
            receipts = current.get('source_receipts', {})
            changed = []
            next_receipts = dict(receipts)
            for source in inputs:
                digest = sha256_file(source)
                if receipts.get(str(source)) == digest:
                    continue
                if source.suffix.lower() == '.lua':
                    archived = archive_source(source, root / 'archives')
                else:
                    archived = source
                changed.append(archived)
                next_receipts[str(source)] = digest
            if not changed and not (publish and not current.get('publication') and pointer.exists()):
                save('unchanged')
                return job
            if changed:
                folder = root / 'generations' / job_id
                folder.mkdir(parents=True)
                database = folder / 'market.duckdb'
                save('importing', candidate_database=str(database), archives=[str(p) for p in changed])
                with redirect_stdout(sys.stderr):
                    policy = market_policy(config, inventory(Path(current['database']))['scans'], current)
                    report = stage_generation(Path(current['database']), database, sources=changed,
                                              memory_limit=config.get('memory_limit', '1GB'),
                                              newer_only=True, allowed_realms=policy['allowed'],
                                              new_realm_after=policy['floor'] if policy['allowed'] is None else None,
                                              excluded_realms=policy['excluded'])
            else:
                database = Path(current['database'])
                folder = database.parent
                report = json.loads(database.with_suffix('.report.json').read_text(encoding='utf-8'))
            context = dict(database=database, report=database.with_suffix('.report.json'),
                           generation=folder, job_id=folder.name,
                           base_sha256=current.get('published_database_sha256', current['database_sha256']),
                           python=sys.executable, config_dir=config.get('config_dir', ''),
                           repo=Path(__file__).resolve().parents[2])
            save('icons')
            icons = prepare_icons(database, folder / 'icon-map.json', config.get('icon_map'), config.get('listfile'),
                                  [Path(current['database']).parent / 'wow-icons'] + ([config['icon_cache']] if config.get('icon_cache') else []))
            previous_state = Path(current['database']).parent / 'icon-source-status.json'
            if not previous_state.is_file() and config.get('icon_state'):
                previous_state = Path(config['icon_state'])
            if previous_state.is_file() and previous_state.parent != folder:
                shutil.copyfile(previous_state, folder / previous_state.name)
            if config.get('icon_sync_command'):
                hook(config['icon_sync_command'], context)
            save('accepting', icons=icons)
            accept(database, report)
            report['icon_manifest_sha256'] = sha256_file(folder / 'icon-map.json')
            write_json(database.with_suffix('.report.json'), report)
            next_current = dict(database=str(database), database_sha256=report['database_sha256'],
                                previous={k: current[k] for k in ('database', 'database_sha256')},
                                source_receipts=next_receipts, publication=None,
                                published_database_sha256=current.get('published_database_sha256', current['database_sha256']))
            next_current['new_realm_after'] = market_policy(config, report['scans'], current)['floor'] if not changed else policy['floor']
            next_current['realm_policy'] = config.get('realm_policy', 'allowlist')
            summary = dict(source_snapshot_count=len(report['scans']),
                           source_record_count=report['listing_count'], added_snapshot_count=len(report['added_scans']),
                           added_snapshots=[dict(realm=s['realm_name'], region_id=s['region_id'], realm_id=s['realm_id'],
                                                timestamp=s['timestamp'], record_count=s['record_count']) for s in report['added_scans']])
            publication = None
            if publish:
                if not config.get('publish_command'):
                    raise ValueError('Explicit publication requires publish_command in local config')
                save('publishing', candidate_database=str(database), pending_current=next_current)
                # Publisher must return a durable receipt for this exact generation.
                publication = json.loads(hook(config['publish_command'], context).stdout)
                if publication.get('status') != 'active' or publication.get('database_sha256') != report['database_sha256']:
                    raise ValueError('Publisher did not confirm the exact candidate generation')
                if config.get('public_url') and not isinstance(publication.get('verification'), dict):
                    publication['verification'] = verify_public_api(config, report['scans'])
            next_current['publication'] = publication
            if publication:
                next_current['published_database_sha256'] = report['database_sha256']
            write_json(pointer, next_current)
            save('published' if publish else 'ready', database_sha256=report['database_sha256'], publication=publication, summary=summary)
            return job
        except Exception as exc:
            phase = 'publication_unknown' if job['phase'] == 'publishing' else 'failed'
            save(phase, error=str(exc))
            raise


def reconcile(root, receipt):
    """Operator supplies the publisher's durable receipt after an interrupted SSH reply."""
    root = Path(root)
    with generation_lock(root / 'update'):
        status = json.loads((root / 'status.json').read_text(encoding='utf-8'))
        current = status.get('pending_current')
        if status['phase'] not in ('publishing', 'publication_unknown') or not current:
            raise ValueError('No unresolved publication')
        if (receipt.get('status') != 'active' or receipt.get('database_sha256') != current['database_sha256']
                or sha256_file(Path(current['database'])) != current['database_sha256']):
            raise ValueError('Receipt does not confirm the pending generation')
        current.update(publication=receipt, published_database_sha256=current['database_sha256'])
        write_json(root / 'current.json', current)
        status.update(phase='published', publication=receipt, updated_at=now())
        write_json(root / 'status.json', status)
        write_json(root / 'jobs' / (status['id'] + '.json'), status)
        return status


def verify_published(config):
    root = Path(config['root'])
    with generation_lock(root / 'update'):
        status_path = root / 'status.json'
        status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
        if status.get('phase') in ('publishing', 'publication_unknown'):
            raise ValueError('Publication outcome is unknown; reconcile the receipt before verification')
        current = json.loads((root / 'current.json').read_text(encoding='utf-8'))
        database = Path(current['database'])
        report = json.loads(database.with_suffix('.report.json').read_text(encoding='utf-8'))
        receipt = current.get('publication') or {}
        if (receipt.get('status') != 'active' or receipt.get('database_sha256') != current['database_sha256']
                or report['database_sha256'] != current['database_sha256'] or sha256_file(database) != current['database_sha256']):
            raise ValueError('Published generation checksum or receipt mismatch')
        verification = verify_public_api(config, report['scans'])
        result = dict(phase='verified', database_sha256=current['database_sha256'], verification=verification,
                      source_snapshot_count=len(report['scans']), source_record_count=report['listing_count'], verified_at=now())
        write_json(root / 'verification.json', result)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True, type=Path)
    parser.add_argument('--source', action='append')
    parser.add_argument('--watch', type=float, metavar='SECONDS')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--reconcile-receipt', type=Path)
    parser.add_argument('--discover-client', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--verify', action='store_true', help='Verify the current published generation without importing or uploading')
    args = parser.parse_args()
    config = load_config(args.config)
    if args.verify:
        print(json.dumps(verify_published(config), ensure_ascii=False))
        return
    if args.reconcile_receipt:
        print(json.dumps(reconcile(config['root'], json.loads(args.reconcile_receipt.read_text(encoding='utf-8')))))
        return
    if args.watch is not None and args.watch < 5:
        parser.error('Watch interval must be at least 5 seconds')
    while True:
        try:
            discovered = discover_client_sources(config) if args.discover_client and not args.source else None
            sources = discovered['sources'] if discovered else args.source
            if args.dry_run:
                print(json.dumps(discovered or dict(sources=sources or config.get('sources', [])), ensure_ascii=False))
                return
            pointer = Path(config['root']) / 'current.json'
            pending = args.publish and pointer.exists() and not json.loads(pointer.read_text(encoding='utf-8')).get('publication')
            if discovered is not None and not sources and not pending:
                result = dict(phase='unchanged', reason='no_new_client_snapshots', discovery=discovered)
            else:
                result = run_update(config, sources, args.publish)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as exc:
            if not args.watch:
                raise
            print(str(exc), file=sys.stderr, flush=True)
        if args.watch is None:
            break
        time.sleep(args.watch)


if __name__ == '__main__':
    main()
