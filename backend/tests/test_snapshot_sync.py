import contextlib
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'deploy'))
from fastapi.testclient import TestClient
from sync_api import create_app
from sync_state import Busy, Store, guard
from compact_market.build import build
from compact_market.export_snapshot import export
from compact_market.jsonl import decode, header, SnapshotRows
from compact_market.manage import stage_generation
from compact_market.sync_import import run as import_snapshot
from app.services.auction_importer import decode_snapshot, validate_snapshot
from tests.test_market_pipeline import _lua_snapshot


class SnapshotSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.lua=self.root/'source.lua';self.lua.write_text(_lua_snapshot(),encoding='utf-8')
        self.source=self.root/'snapshot.jsonl.gz';export(self.lua,self.source)
        self.payload=self.source.read_bytes();self.meta=header(self.source)
        self.manifest={key:self.meta[key] for key in ('schema_version','region_id','realm_id','captured_at','record_count')}
        self.manifest.update(compressed_sha256=hashlib.sha256(self.payload).hexdigest(),size_bytes=len(self.payload))
        self.app=create_app(self.root/'sync',token='t'*48)
        self.client=TestClient(self.app);self.auth={'Authorization':'Bearer '+'t'*48}

    def tearDown(self):
        self.client.close();self.temp.cleanup()

    def create(self,manifest=None):
        return self.client.post('/api/sync/jobs',json=manifest or self.manifest,headers=self.auth)

    def test_authentication_and_small_manifest_validation(self):
        self.assertEqual(self.client.post('/api/sync/jobs',json=self.manifest).status_code,401)
        for values in ({'size_bytes':129*1024*1024},{'realm_id':True},{'captured_at':int(time.time())+10000},{'compressed_sha256':'../escape'}):
            self.assertEqual(self.create(dict(self.manifest,**values)).status_code,422)
        self.assertEqual(self.client.post('/api/sync/jobs',content=b'x'*4097,headers=self.auth).status_code,413)
        self.assertEqual(self.client.get('/api/sync/jobs/'+'a'*32,headers=self.auth).status_code,404)

    def test_duplicate_task_reuses_receipt_and_busy_rejects_before_upload(self):
        first=self.create();self.assertEqual(first.status_code,202)
        self.assertEqual(self.create().json()['id'],first.json()['id'])
        other=self.create(dict(self.manifest,compressed_sha256='a'*64))
        self.assertEqual(other.status_code,409);self.assertEqual(other.headers['Retry-After'],'10')
        self.assertFalse((self.root/'sync/jobs'/first.json()['id']/'snapshot.jsonl.gz').exists())

    def test_upload_checksum_failure_releases_job_and_retry_is_new_attempt(self):
        first=self.create().json()
        result=self.client.put(first['upload_url'],content=b'x'*len(self.payload),headers=dict(self.auth,**{'Content-Type':'application/gzip'}))
        self.assertEqual(result.status_code,422)
        self.assertEqual(self.client.get(first['status_url'],headers=self.auth).json()['phase'],'failed')
        self.assertNotEqual(self.create().json()['id'],first['id'])

    def test_successful_upload_queues_once_and_finished_receipt_is_idempotent(self):
        first=self.create().json()
        response=self.client.put(first['upload_url'],content=self.payload,headers=dict(self.auth,**{'Content-Type':'application/gzip'}))
        self.assertEqual(response.status_code,202);self.assertEqual(response.json()['phase'],'queued')
        self.assertEqual(self.client.put(first['upload_url'],content=b'',headers=self.auth).status_code,200)
        store=self.app.state.store
        store.transition(first['id'],('queued',),'importing')
        store.transition(first['id'],('importing',),'succeeded',result={'changed':False})
        self.assertEqual(self.create().json()['phase'],'succeeded')
        self.assertIsNone(store.active())

    def test_expired_upload_lease_releases_slot_but_unknown_publication_does_not(self):
        first=self.create().json();store=self.app.state.store
        job=store.job(first['id']);job['upload_deadline']=0;store.save(job)
        next_job=self.create(dict(self.manifest,compressed_sha256='b'*64)).json()
        self.assertEqual(store.job(first['id'])['phase'],'failed')
        store.transition(next_job['id'],('awaiting_upload',),'publication_unknown')
        self.assertEqual(self.create().status_code,409)

    def test_jsonl_retains_exact_legacy_fingerprint_and_roundtrip_fields(self):
        old=validate_snapshot(decode_snapshot(self.lua))[0];new=validate_snapshot(decode(self.source))[0]
        self.assertEqual(old.fingerprint,new.fingerprint)
        self.assertIsInstance(new.items,SnapshotRows)
        self.assertEqual([i['itemLink'] for i in new.items],[i['itemLink'] for i in old.items])
        with contextlib.redirect_stdout(io.StringIO()):
            report=build([self.source],self.root/'base.duckdb')
            result=stage_generation(self.root/'base.duckdb',self.root/'candidate.duckdb',sources=[self.lua])
        self.assertEqual(result['added_scans'],[])
        self.assertEqual(report['counts']['listings'],6)

    def test_stream_rejects_declared_count_and_non_integer_prices(self):
        rows=list(decode(self.source)['auctions'].values())[0]['scans'][0]['items']
        records=[self.meta]+list(rows)
        for changed in ('count','price','scope'):
            data=json.loads(json.dumps(records))
            if changed=='count': data[0]['record_count']+=1
            if changed=='price': data[1]['buyoutAmount']=1.5
            if changed=='scope': data[1]['marketScope']='unknown'
            bad=self.root/(changed+'.jsonl.gz')
            with gzip.open(bad,'wt',encoding='utf-8') as output:
                for row in data: output.write(json.dumps(row)+'\n')
            with self.assertRaises(ValueError): validate_snapshot(decode(bad))

    def test_existing_dataset_switch_blocks_task_creation(self):
        lock=self.root/'switch.lock';lock.write_text('123')
        app=create_app(self.root/'other',token='t'*48,switch_lock=lock)
        with TestClient(app) as client:
            self.assertEqual(client.post('/api/sync/jobs',json=self.manifest,headers=self.auth).status_code,409)

    def test_new_snapshot_appends_and_existing_scan_ids_remain_stable(self):
        with contextlib.redirect_stdout(io.StringIO()):
            base_report=build([self.lua],self.root/'base.duckdb')
            base_report['database_sha256']=hashlib.sha256((self.root/'base.duckdb').read_bytes()).hexdigest()
            (self.root/'base.report.json').write_text(json.dumps(base_report))
            self.lua.write_text(_lua_snapshot(timestamp=1700000600),encoding='utf-8')
            newer=self.root/'new.jsonl.gz';export(self.lua,newer)
            manifest=dict(self.manifest,captured_at=1700000600,size_bytes=newer.stat().st_size,
                          compressed_sha256=hashlib.sha256(newer.read_bytes()).hexdigest())
            result=import_snapshot(newer,self.root/'base.duckdb',self.root/'generation/market.duckdb',manifest)
        self.assertEqual(result['added_scans'],1)
        report=json.loads((self.root/'generation/market.report.json').read_text())
        self.assertEqual([s['scan_id'] for s in report['scans']],[1,2])
        self.assertEqual(report['inherited_scan_ids'],[1])
        self.assertFalse(report['summaries_rebuilt'])

    def test_worker_publication_receipt_completes_durable_job_and_releases_lock(self):
        import sync_worker
        store=self.app.state.store;job=self.create().json()
        store.transition(job['id'],('awaiting_upload',),'publishing',base_sha256='base')
        folder=store.root/'jobs'/job['id']/'generation';folder.mkdir()
        (folder/'market.report.json').write_text(json.dumps({'added_scans':[{'scan_id':2}]}))
        config={'root':str(store.root),'switch_lock':str(store.root/'dataset-switch.lock'),
                'nginx_config':'unused','backup_dir':'unused','public_url':'https://example.test','runtime_dir':str(self.root/'runtime')}
        with patch('sync_worker.dataset.activate',return_value={'status':'active'}) as activate, patch('sync_worker.subprocess.call',return_value=0):
            sync_worker.tick(config)
        self.assertEqual(store.job(job['id'])['phase'],'succeeded')
        self.assertTrue(store.job(job['id'])['result']['changed'])
        self.assertIsNone(store.active());self.assertFalse(Path(config['switch_lock']).exists())
        self.assertEqual(activate.call_args.args[0].expected_current_sha,'base')

    def test_worker_distinguishes_confirmed_old_data_from_unknown_publication(self):
        import sync_worker
        store=self.app.state.store
        config={'root':str(store.root),'switch_lock':str(store.root/'dataset-switch.lock'),
                'nginx_config':'unused','backup_dir':'unused','public_url':'https://example.test','runtime_dir':str(self.root/'runtime')}
        for active_sha,expected in (('base','failed'),('different','publication_unknown')):
            job=self.create().json();store.transition(job['id'],('awaiting_upload',),'publishing',base_sha256='base')
            with patch('sync_worker.dataset.activate',side_effect=RuntimeError('interrupted')), patch('sync_worker.live',return_value=Path('unused')), patch('sync_worker.dataset.sha256',return_value=active_sha),contextlib.redirect_stdout(io.StringIO()):
                sync_worker.tick(config)
            self.assertEqual(store.job(job['id'])['phase'],expected)
            self.assertEqual(store.active() is None,expected=='failed')
        self.assertEqual(self.create(dict(self.manifest,compressed_sha256='c'*64)).status_code,409)
