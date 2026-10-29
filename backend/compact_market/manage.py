"""Stage immutable snapshot generations. No writes to a running database or game files."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
import shutil
from pathlib import Path
import tarfile
import tempfile
import uuid

import duckdb

from app.services.auction_importer import sha256_file
from compact_market.build import build, extract_source, update_digest
from compact_market.schema import DESCRIPTOR_FIELDS, FACT_FIELDS, RESTORE_SQL
from compact_market.store import rows_as_dicts


def write_json(path: Path, value: dict):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as target:
            json.dump(value, target, ensure_ascii=False, indent=2)
            target.write('\n')
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def inventory(database: Path) -> dict:
    with duckdb.connect(str(database), read_only=True) as db:
        scans = rows_as_dicts(db.execute('SELECT * FROM scans ORDER BY timestamp, scan_id'))
        for scan in scans:
            scan['scanned_at'] = datetime.fromtimestamp(scan['timestamp'], timezone.utc).isoformat()
        return dict(database=str(database.resolve()), scans=scans,
                    snapshot_count=len(scans), listing_count=sum(s['record_count'] for s in scans),
                    realms=[dict(region_id=r, realm_id=m, realm_name=name) for r, m, name in
                            sorted({(s['region_id'], s['realm_id'], s['realm_name']) for s in scans})])


def archive_source(source: Path, root: Path) -> Path:
    """Copy a stable client Lua into a content-addressed archive; never clear it."""
    source = source.resolve(strict=True)
    before = source.stat()
    digest = sha256_file(source)
    folder = root / digest
    archive = folder / 'auction.lua.tgz'
    manifest_path = folder / 'manifest.json'
    if archive.exists() or manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        if manifest.get('snapshotSha256') != digest or sha256_file(archive) != manifest.get('archiveSha256'):
            raise ValueError('Existing archive does not match its manifest')
        return archive
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.archive-', dir=root) as temporary:
        stage = Path(temporary)
        target = stage / 'auction.lua.tgz'
        with tarfile.open(target, 'w:gz', compresslevel=1) as tar:
            tar.add(source, arcname='auction.lua', recursive=False)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or sha256_file(source) != digest:
            raise ValueError('Client source changed while archiving; retry after it finishes writing')
        # Verify the bytes actually archived, not only the file before/after.
        with tempfile.TemporaryDirectory(dir=stage) as extracted:
            lua, _ = extract_source(target, Path(extracted))
            if sha256_file(lua) != digest:
                raise ValueError('Archived content differs from source')
        write_json(stage / 'manifest.json', dict(version=1, snapshotSha256=digest,
                   archiveSha256=sha256_file(target), sourceSize=before.st_size,
                   createdAt=datetime.now(timezone.utc).isoformat()))
        stage.rename(folder)
    return archive


def _realm_key(value: str) -> tuple[int, int]:
    try:
        region, realm = (int(v) for v in value.split(':'))
        if region < 1 or realm < 1:
            raise ValueError
        return region, realm
    except ValueError:
        raise ValueError('Realm identity must be positive REGION_ID:REALM_ID; market 0 is derived') from None


def _verify_scan(db, scan, original_id=None):
    cursor = db.execute(RESTORE_SQL + ' WHERE f.scan_id = ? ORDER BY f.source_index', [scan['scan_id']])
    names = [d[0] for d in cursor.description]
    actual = hashlib.sha256()
    normalized = hashlib.sha256()
    count = quantity = 0
    while batch := cursor.fetchmany(10000):
        for values in batch:
            row = dict(zip(names, values))
            update_digest(actual, row)
            row['scan_id'] = original_id if original_id is not None else scan['scan_id']
            update_digest(normalized, row)
            count += 1
            quantity += row['quantity']
    if (count != scan['record_count'] or quantity != scan['total_quantity']
            or normalized.hexdigest() != scan['mapping_sha256']):
        raise ValueError(f"Snapshot round-trip failed: {scan['scan_id']}")
    return dict(scan_id=scan['scan_id'], rows=count, total_quantity=quantity,
                mapping_sha256=actual.hexdigest(), all_modeled_fields_match=True)


def stage_generation(base: Path, output: Path, *, sources=(), remove_scans=(), remove_realms=(),
                     keep_scans=None, expected_scans=None, memory_limit='1GB', full_verify=False,
                     newer_only=False, allowed_realms=None) -> dict:
    """Append verified snapshots or remove exact identities into a NEW generation.

    Existing scan IDs are stable, including after pruning. Reimports deduplicate by
    fingerprint; dictionaries deduplicate by all original descriptor/person fields.
    The base file remains a complete rollback copy.
    """
    base = base.resolve(strict=True)
    output = output.resolve()
    if output.exists() or output.with_suffix('.report.json').exists() or base == output:
        raise ValueError('Output must be a new generation path')
    before = inventory(base)
    known = {s['scan_id']: s for s in before['scans']}
    latest_times = {}
    for scan in known.values():
        identity = (scan['region_id'], scan['realm_id'])
        latest_times[identity] = max(latest_times.get(identity, 0), scan['timestamp'])
    remove = set(remove_scans)
    if not remove.issubset(known):
        raise ValueError('Unknown scan ID in removal selection')
    realms = {_realm_key(v) for v in remove_realms}
    if not realms.issubset({(s['region_id'], s['realm_id']) for s in known.values()}):
        raise ValueError('Unknown region/realm identity in removal selection')
    if keep_scans is not None:
        if remove or realms or not set(keep_scans).issubset(known):
            raise ValueError('Keep selection must use known scan IDs and cannot combine with removals')
        remove = set(known) - set(keep_scans)
    remove.update(s['scan_id'] for s in known.values() if (s['region_id'], s['realm_id']) in realms)
    kept = set(known) - remove
    base_sha = sha256_file(base)
    # Only a report bound to the exact immutable file may certify retained rows.
    certified = {}
    base_report = base.with_suffix('.report.json')
    if base_report.is_file() and not full_verify:
        report = json.loads(base_report.read_text(encoding='utf-8'))
        if report.get('database_sha256') == base_sha:
            certified = {c['scan_id']: c for c in report.get('roundtrip_checks', [])
                         if c.get('all_modeled_fields_match')}
    wal = base.with_name(base.name + '.wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Base must be a checkpointed immutable database without pending WAL')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.generation-', dir=output.parent) as temporary:
        folder = Path(temporary)
        incoming = []
        skipped = []
        hashes = {s['snapshot_sha256'] for s in known.values()
                  if all(v['scan_id'] in kept for v in known.values()
                         if v['snapshot_sha256'] == s['snapshot_sha256'])}
        for index, source in enumerate(sources):
            with tempfile.TemporaryDirectory(dir=folder) as extracted:
                lua, _ = extract_source(Path(source), Path(extracted))
                digest = sha256_file(lua)
            if digest in hashes:
                skipped.append(dict(source=str(source), reason='snapshot_already_present'))
                continue
            target = folder / f'incoming-{index}.duckdb'
            build([Path(source)], target, memory_limit=memory_limit)
            incoming.append(target)
            hashes.add(digest)
        candidate = folder / 'candidate.duckdb'
        shutil.copyfile(base, candidate)
        db = duckdb.connect(str(candidate), config={'threads': 2, 'memory_limit': memory_limit})
        checks = []
        added = []
        try:
            has_metadata = db.execute("SELECT count(*) FROM duckdb_tables() WHERE table_name='generation_metadata'").fetchone()[0]
            next_scan_id = (int(db.execute("SELECT value FROM generation_metadata WHERE key='next_scan_id'").fetchone()[0])
                            if has_metadata else max(known, default=0) + 1)
            if remove:
                db.execute('CREATE TEMP TABLE removed_ids (scan_id UINTEGER)')
                db.executemany('INSERT INTO removed_ids VALUES (?)', [(v,) for v in sorted(remove)])
                for table in ('scans', 'scopes', 'listings', 'summaries'):
                    db.execute(f'DELETE FROM {table} WHERE scan_id IN (SELECT scan_id FROM removed_ids)')
            fingerprints = {known[i]['fingerprint'] for i in kept}
            expectations = [(dict(known[i]), None) for i in sorted(kept)]
            for path in incoming:
                db.execute('ATTACH ? AS incoming (READ_ONLY)', [str(path)])
                rows = rows_as_dicts(db.execute('SELECT * FROM incoming.scans ORDER BY scan_id'))
                scan_map = []
                for row in rows:
                    if row['fingerprint'] in fingerprints:
                        skipped.append(dict(fingerprint=row['fingerprint'], reason='scan_already_present'))
                        continue
                    if row['region_id'] is None or row['realm_id'] is None or row['realm_id'] <= 0:
                        raise ValueError('New snapshots require captured region and realm IDs')
                    identity = (row['region_id'], row['realm_id'])
                    if allowed_realms is not None and f'{identity[0]}:{identity[1]}' not in allowed_realms:
                        raise ValueError('Automatic update contains an unconfigured realm')
                    if newer_only and row['timestamp'] <= latest_times.get(identity, -1):
                        raise ValueError('Automatic update cannot restore older snapshots; use explicit snapshot management')
                    original_id = row['scan_id']
                    row['scan_id'] = next_scan_id
                    expectations.append((row, original_id))
                    added.append(dict(row))
                    scan_map.append((original_id, next_scan_id))
                    fingerprints.add(row['fingerprint'])
                    next_scan_id += 1
                if scan_map:
                    db.execute('CREATE TEMP TABLE scan_map (incoming_id UINTEGER, target_id UINTEGER)')
                    db.executemany('INSERT INTO scan_map VALUES (?,?)', scan_map)
                    for table, key, fields in [('descriptors', 'descriptor_id', DESCRIPTOR_FIELDS), ('people', 'person_id', ('value',))]:
                        maximum = db.execute(f'SELECT coalesce(max({key}),0) FROM {table}').fetchone()[0]
                        equality = ' AND '.join(f'i.{name} IS NOT DISTINCT FROM p.{name}' for name in fields)
                        db.execute(f'''CREATE TEMP TABLE {table}_map AS SELECT i.{key} AS incoming_id,
                            coalesce(p.{key}, ? + row_number() OVER (ORDER BY i.{key})) AS target_id
                            FROM incoming.{table} i LEFT JOIN {table} p ON {equality}''', [maximum])
                        db.execute(f'''INSERT INTO {table} BY NAME SELECT m.target_id AS {key}, i.* EXCLUDE ({key})
                            FROM incoming.{table} i JOIN {table}_map m ON i.{key}=m.incoming_id
                            WHERE NOT EXISTS (SELECT 1 FROM {table} p WHERE p.{key}=m.target_id)''')
                    db.execute('''INSERT INTO scans BY NAME SELECT m.target_id AS scan_id, s.* EXCLUDE (scan_id)
                                  FROM incoming.scans s JOIN scan_map m ON s.scan_id=m.incoming_id''')
                    db.execute('''INSERT INTO scopes BY NAME SELECT m.target_id AS scan_id, s.* EXCLUDE (scan_id)
                                  FROM incoming.scopes s JOIN scan_map m ON s.scan_id=m.incoming_id''')
                    # Summaries were already computed and validated in the incoming build.
                    db.execute('''INSERT INTO summaries BY NAME SELECT m.target_id AS scan_id, s.* EXCLUDE (scan_id)
                                  FROM incoming.summaries s JOIN scan_map m ON s.scan_id=m.incoming_id''')
                    projection = []
                    joins = ['JOIN scan_map sm ON f.scan_id=sm.incoming_id',
                             'JOIN descriptors_map dm ON f.descriptor_id=dm.incoming_id']
                    for field in FACT_FIELDS:
                        if field == 'scan_id':
                            projection.append('sm.target_id AS scan_id')
                        elif field == 'descriptor_id':
                            projection.append('dm.target_id AS descriptor_id')
                        elif field in ('owner_id', 'owner_full_name_id', 'bidder_full_name_id'):
                            joins.append(f'JOIN people_map {field}_map ON f.{field}={field}_map.incoming_id')
                            projection.append(f'{field}_map.target_id AS {field}')
                        else:
                            projection.append('f.' + field)
                    db.execute('INSERT INTO listings BY NAME SELECT ' + ','.join(projection) + ' FROM incoming.listings f ' + ' '.join(joins))
                    for table in ('scan_map', 'descriptors_map', 'people_map'):
                        db.execute('DROP TABLE ' + table)
                db.execute('DETACH incoming')
            db.execute('CREATE TABLE IF NOT EXISTS generation_metadata (key VARCHAR, value VARCHAR)')
            db.execute("DELETE FROM generation_metadata WHERE key='next_scan_id'")
            db.execute("INSERT INTO generation_metadata VALUES ('next_scan_id', ?)", [str(next_scan_id)])
            if remove:
                db.execute('DELETE FROM descriptors WHERE descriptor_id NOT IN (SELECT descriptor_id FROM listings)')
                db.execute('''DELETE FROM people WHERE person_id NOT IN (SELECT owner_id FROM listings
                    UNION SELECT owner_full_name_id FROM listings UNION SELECT bidder_full_name_id FROM listings)''')
            if not expectations or (expected_scans is not None and len(expectations) != expected_scans):
                raise ValueError('Resulting snapshot count does not match the required nonempty dataset')
            for scan, original_id in expectations:
                previous = certified.get(scan['scan_id']) if original_id is None else None
                if (previous and previous.get('mapping_sha256') == scan['mapping_sha256']
                        and previous.get('rows') == scan['record_count']
                        and previous.get('total_quantity') == scan['total_quantity']):
                    check = dict(previous, verification='inherited_exact_base_sha256')
                else:
                    check = dict(_verify_scan(db, scan, original_id), verification='full_roundtrip')
                checks.append(check)
                db.execute('UPDATE scans SET mapping_sha256=? WHERE scan_id=?', [check['mapping_sha256'], scan['scan_id']])
                for added_scan in added:
                    if added_scan['scan_id'] == scan['scan_id']:
                        added_scan['mapping_sha256'] = check['mapping_sha256']
            db.execute('CREATE INDEX IF NOT EXISTS listing_lookup ON listings(scan_id, descriptor_id)')
            db.execute('CREATE INDEX IF NOT EXISTS descriptor_lookup ON descriptors(market_key)')
            db.execute('CREATE INDEX IF NOT EXISTS summary_lookup ON summaries(scan_id, market_key)')
            db.execute('CHECKPOINT')
        finally:
            db.close()
        if sha256_file(base) != base_sha:
            raise ValueError('Base generation changed during staging')
        result = inventory(candidate)
        result.update(format_version=1, database=str(output), base_database=str(base), base_sha256=base_sha,
                      database_sha256=sha256_file(candidate), kept_scan_ids=sorted(kept),
                      removed_scans=[known[i] for i in sorted(remove)], added_scans=added,
                      skipped=skipped, roundtrip_checks=checks, created_at=datetime.now(timezone.utc).isoformat())
        result.update(storage_mode='immutable_clone_append', summaries_rebuilt=False,
                      full_verify=full_verify,
                      inherited_scan_ids=[c['scan_id'] for c in checks if c['verification'] == 'inherited_exact_base_sha256'])
        result['toolchain_sha256'] = {name: sha256_file(Path(__file__).with_name(name))
                                     for name in ('manage.py', 'build.py', 'schema.py')}
        # Exclusive output lock prevents two writers from publishing the same path.
        with generation_lock(output):
            if output.exists():
                raise ValueError('Output generation was created by another operation')
            write_json(output.with_suffix('.report.json'), result)
            try:
                candidate.rename(output)
            except Exception:
                output.with_suffix('.report.json').unlink(missing_ok=True)
                raise
        return result


@contextmanager
def generation_lock(path):
    lock = path.with_name(path.name + '.lock')
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, str(os.getpid()).encode('ascii'))
        yield
    finally:
        os.close(fd)
        lock.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('list')
    listing.add_argument('--database', required=True, type=Path)
    archive = sub.add_parser('archive')
    archive.add_argument('source', type=Path)
    archive.add_argument('--archive-root', required=True, type=Path)
    stage = sub.add_parser('stage')
    stage.add_argument('--database', required=True, type=Path)
    stage.add_argument('--output', required=True, type=Path)
    stage.add_argument('--add', action='append', default=[], type=Path)
    stage.add_argument('--remove-source-scan', dest='remove_scan', action='append', default=[], type=int)
    stage.add_argument('--remove-realm', action='append', default=[])
    stage.add_argument('--keep-source-scan', dest='keep_scan', action='append', type=int)
    stage.add_argument('--expect-scans', type=int)
    stage.add_argument('--memory-limit', default='1GB')
    stage.add_argument('--full-verify', action='store_true', help='Audit all retained rows, even with a matching base report')
    args = parser.parse_args()
    if args.command == 'list':
        result = inventory(args.database)
    elif args.command == 'archive':
        result = {'archive': str(archive_source(args.source, args.archive_root))}
    else:
        result = stage_generation(args.database, args.output, sources=args.add,
                                  remove_scans=args.remove_scan, remove_realms=args.remove_realm,
                                  keep_scans=args.keep_scan, expected_scans=args.expect_scans,
                                  memory_limit=args.memory_limit, full_verify=args.full_verify)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
