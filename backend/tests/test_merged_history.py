import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from compact_market.build import build
from compact_market.unified import UnifiedMarketStore, ArrowRows
from tests.test_market_pipeline import _lua_snapshot


class MergedHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();root=Path(cls.temp.name);sources=[]
        for realm,offsets in [(123,list(range(100))),(456,[10,20,30,45]),(789,[1])]:
            for offset in offsets:
                path=root/('%s-%s.lua'%(realm,offset))
                path.write_text(_lua_snapshot(timestamp=1700000000+offset*3600,realm_id=realm,realm_name='Realm '+str(realm)),encoding='utf-8')
                sources.append(path)
        with contextlib.redirect_stdout(io.StringIO()):build(sources,root/'history.duckdb')
        cls.store=UnifiedMarketStore(root/'history.duckdb')
        cls.item=cls.store.items(q='20',group_markets=True)['items'][0]

    @classmethod
    def tearDownClass(cls):cls.store.close();cls.temp.cleanup()

    def test_history_caps_before_decoding_and_reserves_each_realms_latest(self):
        decoded=[];original=ArrowRows.__getitem__
        def track(table,index):decoded.append(index);return original(table,index)
        with patch.object(ArrowRows,'__getitem__',track):
            result=self.store.public_history(scan_id=self.item['scanId'],market_key=self.item['marketKey'],all_markets=True,max_points=2000)
        self.assertEqual(len(result['points']),60)
        self.assertEqual(result['pointCount'],105)
        self.assertLessEqual(len(decoded),61)
        for realm in (123,456,789):
            latest=next(sid for sid in self.store.latest_scans if self.store.scans[sid]['realm_id']==realm)
            self.assertIn(latest,{p['scanId'] for p in result['points']})

    def test_latest_listings_exclude_old_snapshots_and_shared_goods_do_not_duplicate(self):
        result=self.store.listings(scan_id=self.item['scanId'],market_key=self.item['marketKey'],all_markets=True,page_size=2)
        second=self.store.listings(scan_id=self.item['scanId'],market_key=self.item['marketKey'],all_markets=True,page=2,page_size=2)
        self.assertEqual(result['total'],3)
        self.assertEqual(result['totalPages'],2)
        self.assertEqual({r['realmID'] for r in result['items']+second['items']},{123,456,789})
        latest={s['scan_id'] for s in self.store.scans.values() if s['scan_id'] in self.store.latest_scans}
        self.assertTrue(all(r['scanId'] in latest for r in result['items']+second['items']))
        shared=self.store.items(q='10',group_markets=True)['items'][0]
        shared_result=self.store.listings(scan_id=shared['scanId'],market_key=shared['marketKey'],all_markets=True)
        self.assertEqual(shared_result['total'],2)
        self.assertFalse(shared_result.get('allMarkets',False))

    def test_single_realm_historical_cutoff_and_range_are_preserved(self):
        old=self.store.items(scan_id=7,q='20')['items'][0]
        result=self.store.public_history(scan_id=old['scanId'],market_key=old['marketKey'])
        self.assertEqual(len(result['points']),3)
        self.assertEqual({p['realmID'] for p in result['points']},{123})
        self.assertTrue(all(p['scannedAtUnix']<=old['scannedAtUnix'] for p in result['points']))
        latest=self.store.items(scan_id=201,q='20')['items'][0]
        recent=self.store.public_history(scan_id=latest['scanId'],market_key=latest['marketKey'],days=7,start=1700000000+95*3600)
        self.assertEqual(len(recent['points']),5)

    def test_history_can_select_one_realm_without_changing_listing_scope(self):
        result=self.store.public_history(scan_id=self.item['scanId'],market_key=self.item['marketKey'],
                                         all_markets=True,history_realm_id=456)
        self.assertEqual({p['realmID'] for p in result['points']},{456})
        self.assertEqual(len(result['points']),4)
        self.assertEqual(result['historyScope']['realmTotal'],1)
        self.assertEqual(result['realmID'],456)
        self.assertFalse(result['historyScope']['allMarkets'])
        with self.assertRaises(ValueError):
            self.store.public_history(scan_id=self.item['scanId'],market_key=self.item['marketKey'],all_markets=True,history_realm_id=999)

    def test_more_than_sixty_realms_are_paged_without_silently_dropping_them(self):
        store=self.store;old_latest=store.latest_scans;added=[]
        try:
            for i in range(61):
                sid=10000+i;realm=1000+i
                store.scans[sid]={**store.scans[self.item['scanId']],'scan_id':sid,'realm_id':realm,'realm_name':'Extra '+str(i)}
                store.scan_timelines[5,realm]=[(store.scans[sid]['timestamp'],sid)];added.append(sid)
            store.latest_scans=(*old_latest,*added)
            first=store.public_history(scan_id=self.item['scanId'],market_key=self.item['marketKey'],all_markets=True)
            second=store.public_history(scan_id=self.item['scanId'],market_key=self.item['marketKey'],all_markets=True,realm_page=2)
            self.assertEqual(first['historyScope']['realmTotal'],64)
            self.assertEqual(first['historyScope']['realmPages'],2)
            self.assertEqual(len(first['points']),60)
            self.assertEqual(len({p['realmID'] for p in first['points']+second['points']}),64)
            self.assertLessEqual(len(second['points']),60)
        finally:
            store.latest_scans=old_latest
            for sid in added:
                realm=store.scans[sid]['realm_id'];del store.scan_timelines[5,realm];del store.scans[sid]
