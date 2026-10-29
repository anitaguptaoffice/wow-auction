"""wow-runner compatible compact importer. Configure COMPACT_UPDATE_CONFIG locally."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys

from app.services.auction_importer import sha256_file, decode_snapshot, validate_snapshot
from compact_market.manage import inventory
from compact_market.update import load_config, run_update


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    try:
        config = load_config(os.environ['COMPACT_UPDATE_CONFIG'])
        source = args.source.resolve(strict=True)
        digest = sha256_file(source)
        pointer = Path(config['root']) / 'current.json'
        before = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else None
        baseline = inventory(Path(before['database'] if before else config['seed_database']))
        old_ids = {s['scan_id'] for s in baseline['scans']}
        with redirect_stdout(sys.stderr):
            job = run_update(config, [source], publish=bool(config.get('publish', False)))
        current = json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {'database': config['seed_database']}
        scans = inventory(Path(current['database']))['scans']
        matched = [s for s in scans if s['snapshot_sha256'] == digest]
        if not matched:
            # Same scan may have been re-exported with different Lua whitespace.
            fingerprints = {s.fingerprint for s in validate_snapshot(decode_snapshot(source))}
            matched = [s for s in scans if s['fingerprint'] in fingerprints]
            if len(matched) != len(fingerprints):
                raise ValueError('Source scan reconciliation failed')
        added = [s for s in matched if s['scan_id'] not in old_ids]
        result = dict(ok=True, snapshot_sha256=digest, source_scan_count=len(matched),
                      imported_scan_count=len(added), skipped_scan_count=len(matched)-len(added),
                      imported_listing_count=sum(s['record_count'] for s in added),
                      existing_listing_count=sum(s['record_count'] for s in matched if s['scan_id'] in old_ids),
                      duplicate_snapshot=not added, scan_ids=[s['scan_id'] for s in matched], phase=job['phase'])
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps(dict(ok=False, error=str(exc)), ensure_ascii=False))
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
