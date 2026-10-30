"""Plan and stage immutable daily snapshot retention without touching the live file."""
import argparse
from collections import defaultdict
from datetime import datetime
import json
from pathlib import Path
import time
from zoneinfo import ZoneInfo

from app.services.auction_importer import sha256_file
from compact_market.manage import inventory, stage_generation, write_json
from compact_market.update import accept, prepare_icons
from retention_policy import validate


def plan(scans, policy, now=None, registry=None):
    policy = validate(policy)
    zone = ZoneInfo(policy['timezone'])
    now = int(time.time() if now is None else now)
    cutoff = now - policy['retention_days'] * 86400
    buckets = defaultdict(list)
    expired = []
    for scan in scans:
        if scan['timestamp'] < cutoff:
            expired.append(scan['scan_id'])
            continue
        day = datetime.fromtimestamp(scan['timestamp'], zone).date().isoformat()
        identity = (registry.resolve(scan)['auction_market_id'] if registry else
                    scan.get('auction_market_id', 'retail:%s:realm:%s' % (scan['region_id'], scan['realm_id'])))
        buckets[identity, day].append(scan)
    protected, candidates, days = set(), [], []
    for (market, day), rows in sorted(buckets.items()):
        rows.sort(key=lambda s: (s['timestamp'], s['scan_id']), reverse=True)
        selected = [s['scan_id'] for s in rows[:policy['min_snapshots_per_day']]]
        protected.update(selected)
        candidates.extend(rows[policy['min_snapshots_per_day']:])
        days.append(dict(auction_market_id=market, date=day, available=len(rows),
                         protected_scan_ids=selected, source_realm_ids=sorted({s['realm_id'] for s in rows}),
                         kept_scan_ids=[s['scan_id'] for s in rows]))
    eligible = len(scans) - len(expired)
    excess = max(0, eligible - policy['max_snapshots'])
    candidates.sort(key=lambda s: (s['timestamp'], s['scan_id']))
    redundant = [s['scan_id'] for s in candidates[:excess]]
    removed = set(expired + redundant)
    kept = [s['scan_id'] for s in scans if s['scan_id'] not in removed]
    for day in days:
        day['kept_scan_ids'] = [sid for sid in day['kept_scan_ids'] if sid not in removed]
    return dict(policy=policy, evaluated_at=now, cutoff=cutoff,
                protected_scan_ids=sorted(protected), cap_exceeded_by=max(0, len(kept)-policy['max_snapshots']),
                capacity_pressure=excess > 0,
                kept_scan_ids=sorted(kept), expired_scan_ids=sorted(expired),
                redundant_scan_ids=sorted(redundant),
                remove_scan_ids=sorted(expired + redundant), days=days,
                before_count=len(scans), after_count=len(kept))


def plan_database(base, policy, now=None):
    data = inventory(base)
    selection = plan(data['scans'], policy, now)
    selection.update(market_registry_sha256=data['market_registry_sha256'],
                     market_registry_version=data['market_registry_version'])
    return selection


def run(base, output, policy, now=None):
    selection = plan_database(base, policy, now)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not selection['remove_scan_ids']:
        report = dict(retention=selection, added_scans=[], removed_scans=[],
                      changed=False, database_sha256=sha256_file(base))
    else:
        report = stage_generation(base, output, keep_scans=selection['kept_scan_ids'],
                                  allow_empty=True, compact=True, memory_limit='512MB')
        # Retain the generation's resolved names as well as its cached bitmaps.
        source_map = base.parent / 'icon-map.json'
        if not source_map.is_file():
            source_map = Path(__file__).parents[1] / 'app' / 'icon-map.json'
        prepare_icons(output, output.parent / 'icon-map.json', source_map=source_map,
                      cache_dirs=[base.parent / 'wow-icons', Path('/app/icon-assets')])
        report.update(retention=selection, changed=True,
                      icon_manifest_sha256=sha256_file(output.parent / 'icon-map.json'))
        accept(output, report)
    write_json(output.with_suffix('.report.json'), report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    policy = json.loads(args.manifest.read_text())['retention']
    result = (run(args.base, args.output, policy) if args.output else
              plan_database(args.base, policy))
    print(json.dumps(result, ensure_ascii=False))
