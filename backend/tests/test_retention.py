"""Retention boundaries, immutable staging, API behavior and shared worker leases."""
import contextlib
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'deploy'))

from app.services.auction_importer import sha256_file
from compact_market.build import build
from compact_market.manage import inventory, stage_generation
from compact_market.retention import plan, run
from compact_market.unified import UnifiedMarketStore
from retention_policy import validate
from sync_state import Busy, Store, read, write
import sync_worker
from tests.test_market_pipeline import _lua_snapshot


def timestamp(value):
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


class RetentionPlanTest(unittest.TestCase):
    def test_capacity_is_a_trigger_not_a_daily_target(self):
        now = timestamp('2026-10-30T12:00:00')
        scans = [dict(scan_id=i, timestamp=now-300+i, region_id=5, realm_id=707) for i in range(1,206)]
        at_limit = plan(scans[:200], {}, now)
        self.assertEqual(at_limit['remove_scan_ids'], [])
        over = plan(scans, {}, now)
        self.assertEqual(over['remove_scan_ids'], [1,2,3,4,5])
        self.assertEqual(over['after_count'], 200)
        self.assertEqual(over['protected_scan_ids'], [205])
        self.assertFalse(plan([], {}, now)['capacity_pressure'])

    def test_window_boundary_daily_latest_realms_and_missing_dates(self):
        now = timestamp('2026-10-30T12:00:00')
        cutoff = now - 14*86400
        scans = [dict(scan_id=i, timestamp=t, region_id=r, realm_id=m) for i,t,r,m in [
            (1, cutoff-1, 5, 707), (2, cutoff, 5, 707),
            (3, now-600, 5, 707), (4, now-300, 5, 707), (5, now-300, 5, 707),
            (6, now-900, 5, 741), (7, now-900, 1, 707), (8, cutoff-100, 5, 999)]]
        result = plan(scans, {'max_snapshots':4}, now)
        self.assertEqual(result['kept_scan_ids'], [2, 5, 6, 7])
        self.assertEqual(result['expired_scan_ids'], [1, 8])
        self.assertEqual(result['redundant_scan_ids'], [3, 4])
        self.assertEqual(len(result['days']), 4)
        self.assertTrue(all(day['kept_scan_ids'] for day in result['days']))
        twice = plan(scans, {'min_snapshots_per_day': 2, 'max_snapshots':4}, now)
        self.assertEqual(twice['kept_scan_ids'], [2, 4, 5, 6, 7])

    def test_timezone_midnight_and_no_data_are_not_filled(self):
        scans = [dict(scan_id=i, timestamp=timestamp(t), region_id=5, realm_id=707)
                 for i,t in [(1,'2026-10-29T15:59:59'),(2,'2026-10-29T16:00:00')]]
        now = timestamp('2026-10-30T00:00:00')
        self.assertEqual(plan(scans, {}, now)['kept_scan_ids'], [1,2])
        self.assertEqual(plan(scans, {'timezone':'UTC', 'max_snapshots':1}, now)['kept_scan_ids'], [2])
        self.assertEqual(plan([], {}, now)['days'], [])

    def test_invalid_policy_cannot_disable_daily_coverage_or_extend_limit(self):
        for policy in ({'min_snapshots_per_day':0}, {'min_snapshots_per_day':True},
                       {'retention_days':15}, {'retention_days':0}, {'unknown':1},
                       {'max_snapshots':0}, {'max_snapshots':True}, {'snapshots_per_day':1}):
            with self.assertRaises(ValueError): validate(policy)
        with self.assertRaises(Exception): plan([], {'timezone':'Not/AZone'})


class RetentionGenerationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = timestamp('2026-10-30T12:00:00')
        self.sources = []
        for i,t,realm in [(1,self.now-15*86400,707),(2,self.now-600,707),
                          (3,self.now-300,707),(4,self.now-500,741)]:
            path = self.root/('%d.lua'%i)
            path.write_text(_lua_snapshot(timestamp=t, realm_id=realm, realm_name=str(realm)))
            self.sources.append(path)
        self.base = self.root/'market.duckdb'
        with contextlib.redirect_stdout(io.StringIO()): build(self.sources, self.base)
        self.original_sha = sha256_file(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def test_compact_selection_preserves_shared_markets_and_is_idempotent(self):
        output = self.root/'next/market.duckdb'
        report = run(self.base, output, {'max_snapshots':2}, self.now)
        selected = [s for s in inventory(self.base)['scans'] if s['timestamp'] in (self.now-300,self.now-500)]
        self.assertEqual(report['kept_scan_ids'], sorted(s['scan_id'] for s in selected))
        self.assertEqual(sha256_file(self.base), self.original_sha)
        self.assertEqual(report['storage_mode'], 'immutable_compact')
        store = UnifiedMarketStore(output)
        try:
            self.assertEqual({r['realmID'] for r in store.catalog()['realms']}, {0,707,741})
            self.assertEqual(len(next(r for r in store.catalog()['realms'] if r['realmID'] == 0)['scans']), 2)
            self.assertTrue(store.items(group_markets=True)['items'])
        finally: store.close()
        no_op = self.root/'noop/market.duckdb'
        result = run(output, no_op, {}, self.now)
        self.assertFalse(result['changed'])
        self.assertFalse(no_op.exists())

    def test_all_expired_serves_empty_and_new_snapshot_ids_are_not_reused(self):
        output = self.root/'empty/market.duckdb'
        report = run(self.base, output, {}, self.now+15*86400)
        self.assertEqual(report['snapshot_count'], 0)
        store = UnifiedMarketStore(output)
        try:
            self.assertEqual(store.catalog(), {'realms':[]})
            self.assertFalse(store.status()['available'])
            self.assertEqual(store.items()['total'], 0)
            self.assertEqual(store.items(group_markets=True)['items'], [])
            with self.assertRaises(KeyError): store.items(scan_id=2)
        finally: store.close()
        with contextlib.redirect_stdout(io.StringIO()):
            restored = stage_generation(output,self.root/'restored.duckdb',sources=[self.sources[-1]])
        self.assertGreater(restored['scans'][0]['scan_id'], 4)


class RetentionLeaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'sync', self.root/'switch.lock')
        self.manifest = dict(compressed_sha256='a'*64, captured_at=100)

    def tearDown(self):
        self.temp.cleanup()

    def test_timer_and_upload_share_durable_reservation(self):
        job = self.store.schedule_retention({})
        self.assertEqual(Store(self.store.root).active()['id'], job['id'])
        with self.assertRaises(Busy): self.store.create(self.manifest)
        with self.assertRaises(Busy): self.store.schedule_retention({})
        self.store.transition(job['id'], ('queued',), 'failed')
        upload,_ = self.store.create(self.manifest)
        with self.assertRaises(Busy): self.store.schedule_retention({})
        self.store.transition(upload['id'], ('awaiting_upload',), 'failed')
        self.store.switch_lock.write_text('123')
        with self.assertRaises(Busy): self.store.schedule_retention({})

    def test_expired_upload_is_rejected_but_success_receipt_remains_idempotent(self):
        job,_ = self.store.create(self.manifest)
        self.store.transition(job['id'], ('awaiting_upload',), 'succeeded')
        write(self.store.root/'retention-policy.json', validate({}))
        self.assertEqual(self.store.create(self.manifest)[0]['id'], job['id'])
        with self.assertRaisesRegex(ValueError, 'retention window'):
            self.store.create(dict(self.manifest, compressed_sha256='b'*64))

    def test_worker_noop_does_not_publish_and_recovers_existing_container(self):
        job = self.store.schedule_retention({})
        job = self.store.transition(job['id'], ('queued',), 'importing',base_sha256='base',base_database='/base/market.duckdb')
        output = self.store.root/'jobs'/job['id']/'generation'
        output.mkdir()
        write(output/'market.report.json', dict(changed=False,added_scans=[],retention={'remove_scan_ids':[]}))
        config = dict(root=str(self.store.root),switch_lock=str(self.store.switch_lock),runtime_dir=str(self.root/'runtime'))
        def command(*args):
            if args[1]=='inspect': return json.dumps([{'State':{'Status':'exited'}}])
            if args[1]=='wait': return '0'
            if args[1]=='logs': return ''
            raise AssertionError(args)
        with patch('sync_worker.subprocess.call',return_value=0), patch('sync_worker.dataset.command',side_effect=command), patch('sync_worker.dataset.activate') as activate, patch('sync_worker.retention_gc',return_value={}):
            sync_worker.tick(config)
        activate.assert_not_called()
        self.assertEqual(self.store.job(job['id'])['phase'], 'succeeded')
        self.assertFalse(self.store.job(job['id'])['result']['changed'])

    def test_gc_preserves_active_recent_rollback_and_receipts(self):
        from retention_gc import collect
        root = self.store.root/'jobs'
        backup = self.root/'backups'
        backup.mkdir()
        now = 200000
        for name in ('old', 'active', 'rollback', 'unfinished'):
            folder = root/name
            folder.mkdir()
            (folder/'generation').mkdir()
            (folder/'generation/market.duckdb').write_bytes(b'database')
            (folder/'snapshot.jsonl.gz').write_bytes(b'snapshot')
            write(folder/'job.json', dict(id=name,phase='importing' if name=='unfinished' else 'succeeded',updated_at=1))
        write(backup/'recent.json', dict(status='active',previous_database=str(root/'rollback/generation/market.duckdb')))
        containers = {
            'live-api': dict(State={'Running':True},Mounts=[dict(Type='bind',Source=str(root/'active/generation/market.duckdb'))]),
            'wow-auction-data-old': dict(State={'Running':False},Mounts=[dict(Type='bind',Source=str(root/'old/generation/market.duckdb'))])}
        removed_containers=[]
        def command(*args):
            if args[1]=='ps': return '\n'.join(containers)
            if args[1]=='inspect': return json.dumps([containers[args[2]]])
            if args[1]=='rm': removed_containers.append(args[2]); return args[2]
            raise AssertionError(args)
        config=dict(root=str(self.store.root),backup_dir=str(backup))
        job=dict(id='current',retention=validate({}))
        with patch('retention_gc.dataset.command',side_effect=command):
            preview=collect(config,job,now,dry_run=True)
            self.assertTrue((root/'old/generation').exists())
            result=collect(config,job,now)
            again=collect(config,job,now)
        self.assertEqual(preview['reclaimed_bytes'],16)
        self.assertEqual(result['reclaimed_bytes'],16)
        self.assertEqual(again['reclaimed_bytes'],0)
        self.assertEqual(removed_containers,['wow-auction-data-old'])
        self.assertTrue((root/'old/job.json').exists())
        self.assertFalse((root/'old/generation').exists())
        for name in ('active','rollback','unfinished'):
            self.assertTrue((root/name/'generation/market.duckdb').exists())


if __name__ == '__main__':
    unittest.main()
