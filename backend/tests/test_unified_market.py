"""Market identity and shared commodity history contracts."""
import unittest
import tempfile
import contextlib
import io
from pathlib import Path
from compact_market.build import build
from tests.test_market_pipeline import _lua_snapshot
import tests.test_compact_market as fixtures
from compact_market.unified import UnifiedMarketStore


class UnifiedMarketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.CompactMarketTest.setUpClass()
        cls.store = UnifiedMarketStore(fixtures.CompactMarketTest.database, memory_limit="1GB")

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        fixtures.CompactMarketTest.tearDownClass()

    def test_commodity_has_one_market_and_cross_source_history(self):
        catalog = self.store.catalog()['realms']
        shared = [m for m in catalog if m['realmID'] == 0]
        self.assertEqual(len(shared), 1)
        self.assertEqual(shared[0]['realm'], '全服')
        self.assertEqual(shared[0]['latestScanId'], 6)
        rows = self.store.items(scan_id=6)['items']
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['totalQuantity'], 5)
        self.assertEqual(rows[0]['listingCount'], 2)
        history = self.store.history(scan_id=6, market_key=rows[0]['marketKey'])
        self.assertEqual([p['source_scan_id'] for p in history['points']], [1, 2, 3])
        self.assertEqual([p['total_quantity'] for p in history['points']], [5, 5, 5])
        self.assertEqual(len(self.store.history(scan_id=4, market_key=rows[0]['marketKey'])['points']), 2)
        detail = self.store.listings(scan_id=6, market_key=rows[0]['marketKey'])
        self.assertEqual(detail['total'], 2)
        self.assertEqual({r['scan_id'] for r in detail['items']}, {3})

    def test_realm_goods_are_isolated_and_commodity_not_duplicated(self):
        self.assertEqual(self.store.items(scan_id=5, q='矿')['total'], 0)
        row = self.store.items(scan_id=5, q='20')['items'][0]
        self.assertEqual([p['source_scan_id'] for p in self.store.history(scan_id=5, market_key=row['marketKey'])['points']], [1, 2])
        self.assertEqual([p['source_scan_id'] for p in self.store.history(scan_id=7, market_key=row['marketKey'])['points']], [3])
        pets = self.store.items(scan_id=5, q='宠物甲')['items']
        self.assertEqual(len(pets), 2)
        self.assertNotEqual(pets[0]['marketKey'], pets[1]['marketKey'])

    def test_column_storage_and_search(self):
        self.assertFalse(hasattr(self.store, 'public_records'))
        self.assertLess(self.store.records.table.nbytes, 100000)
        for query in ('试矿', '矿', '10'):
            self.assertEqual(self.store.items(scan_id=6, q=query)['total'], 1)
        self.assertEqual(self.store.items(scan_id=6, q="%' OR 1=1 --")['total'], 0)
        self.assertEqual(self.store.items(scan_id=2, q='试矿')['total'], 1)
        self.assertNotIn((2, 'price_asc'), self.store.sorted_ids)
        self.assertIn((2, 'price_asc'), self.store.historical_sorts)

    def test_combined_latest_market_and_public_history(self):
        rows = self.store.items(page_size=100)['items']
        self.assertEqual({r['scanId'] for r in rows}, {5, 6, 7})
        self.assertEqual(sum(r['itemID'] == 10 for r in rows), 1)
        self.assertEqual(self.store.items(collection='raid_boe_12_1')['total'], 0)
        with self.assertRaises(ValueError):
            self.store.items(collection='invalid')
        ore = next(r for r in rows if r['itemID'] == 10)
        history = self.store.public_history(scan_id=6, market_key=ore['marketKey'])
        self.assertEqual(history['pointCount'], 3)
        self.assertEqual(history['change']['minUnitPrice']['absolute'], 0)
        self.assertEqual(history['points'][-1]['realmID'], 0)
        detail = self.store.listings(scan_id=6, market_key=ore['marketKey'])
        self.assertIn('displayColumns', detail)
        self.assertEqual(detail['itemID'], 10)

    def test_frontend_http_contract(self):
        from unittest.mock import patch
        from fastapi.testclient import TestClient
        from compact_market.api import app
        with patch.dict('os.environ', {'COMPACT_DATABASE': str(fixtures.CompactMarketTest.database),
                                       'COMPACT_ENGINE': 'unified', 'COMPACT_MEMORY_LIMIT': '1GB'}):
            with TestClient(app) as client:
                result = client.get('/api/market/items').json()
                self.assertIsNone(result['scanId'])
                ore = next(row for row in result['items'] if row['itemID'] == 10)
                params = {'scan_id': ore['scanId'], 'market_key': ore['marketKey']}
                history = client.get('/api/market/history/view', params=params)
                self.assertEqual(history.status_code, 200)
                self.assertEqual(history.json()['pointCount'], 3)
                self.assertIn('minUnitPrice', history.json()['change'])
                detail = client.get('/api/market/listings', params=params)
                self.assertEqual(detail.status_code, 200)
                self.assertIn('displayColumns', detail.json())
                self.assertEqual(client.get('/api/market/items', params={'collection': 'bad'}).status_code, 422)
                catalog = client.get('/api/market/catalog').json()['realms']
                for realm in catalog:
                    for snapshot in realm['scans']:
                        sid = snapshot['scanId']
                        status = client.get('/api/market/status', params={'scan_id': sid}).json()
                        self.assertEqual(status['realmID'], realm['realmID'])
                        self.assertEqual(status['scannedAt'], snapshot['scannedAt'])
                        rows = client.get('/api/market/items', params={'scan_id': sid}).json()['items']
                        self.assertTrue(all(row['realmID'] == realm['realmID'] and row['scanId'] == sid for row in rows))
                self.assertEqual(client.get('/api/market/history/view', params={**params, 'max_points': 10000}).status_code, 422)
                self.assertEqual(client.get('/api/market/compare', params={'market_key': ore['marketKey']}).status_code, 200)
                self.assertEqual(client.get('/api/market/history/view', params={'market_key': 'missing', 'scan_id': 6}).status_code, 404)

    def test_grouping_retains_pet_identities_and_does_not_invent_bid_only_prices(self):
        flat = self.store.items(page_size=100)['items']
        grouped = self.store.items(group_markets=True, page_size=100)
        self.assertEqual(grouped['total'], len({(r['regionID'],r['marketKey'],r['marketScope']) for r in flat}))
        bid_only = next(r for r in grouped['items'] if r['itemID'] == 20)
        self.assertIsNone(bid_only['minUnitPrice'])
        self.assertIsNone(bid_only['marketGroup']['minBuyout'])
        self.assertEqual(bid_only['marketGroup']['listingCount'], 2)
        pets = [r for r in grouped['items'] if r.get('petVariantKey')]
        self.assertEqual(len({r['petVariantKey'] for r in pets}), len(pets))

    def test_group_minimum_unit_and_buyout_prices_keep_their_own_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for realm, rid, quantity, buyout in [('A',123,10,100),('B',456,1,20)]:
                content = _lua_snapshot(realm_name=realm,realm_id=rid)
                content = content.replace('["name"] = "仅竞价物品", ["quantity"] = 1', f'["name"] = "仅竞价物品", ["quantity"] = {quantity}')
                content = content.replace('["buyoutAmount"] = 0, ["bidAmount"] = 0, ["timeLeftBand"] = 2', f'["buyoutAmount"] = {buyout}, ["bidAmount"] = 0, ["timeLeftBand"] = 2')
                source = root / f'{rid}.lua'
                source.write_text(content,encoding='utf-8')
                paths.append(source)
            with contextlib.redirect_stdout(io.StringIO()):
                build(paths,root/'grouped.duckdb')
            store = UnifiedMarketStore(root/'grouped.duckdb')
            try:
                row = store.items(q='20',group_markets=True)['items'][0]
                self.assertEqual((row['realm'],row['minUnitPrice'],row['minBuyout']), ('A',10,100))
                self.assertEqual((row['marketGroup']['minBuyout'],row['marketGroup']['buyoutRealm'],row['marketGroup']['totalQuantity']), (20,'B',11))
                groups = store.items(q='矿',group_markets=True)['items']
                self.assertEqual(len(groups),1)
                self.assertEqual(groups[0]['marketGroup']['marketCount'],1)
            finally:
                store.close()

    def test_grouping_never_merges_regions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            paths = []
            for region in (3,5):
                source = root / f'{region}.lua'
                source.write_text(_lua_snapshot().replace('["regionID"] = 5', f'["regionID"] = {region}'),encoding='utf-8')
                paths.append(source)
            with contextlib.redirect_stdout(io.StringIO()):
                build(paths, root/'regions.duckdb')
            store = UnifiedMarketStore(root/'regions.duckdb')
            try:
                rows = store.items(q='20',group_markets=True)['items']
                self.assertEqual({r['regionID'] for r in rows}, {3,5})
                self.assertEqual(len(rows),2)
                self.assertTrue(all(r['marketGroup']['marketCount']==1 for r in rows))
            finally:
                store.close()

    def test_new_item_is_discovered_without_catalog_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / 'old.lua'
            new = root / 'new.lua'
            old.write_text(_lua_snapshot())
            new.write_text(_lua_snapshot(timestamp=1700086400).replace(
                '["itemID"] = 10', '["itemID"] = 999000').replace(
                '[10] = "region"', '[999000] = "region"').replace(
                'item:10', 'item:999000').replace('测试矿石', 'New Expansion Ore'))
            with contextlib.redirect_stdout(io.StringIO()):
                report = build([old, new], root / 'new.duckdb')
            store = UnifiedMarketStore(root / 'new.duckdb')
            try:
                self.assertEqual(store.items(scan_id=2, q='999000')['total'], 0)
                row = store.items(scan_id=4, q='999000')['items'][0]
                self.assertEqual(row['name'], 'New Expansion Ore')
                self.assertEqual(row['marketScope'], 'region')
                self.assertEqual(len(store.history(scan_id=4, market_key=row['marketKey'])['points']), 1)
                self.assertEqual(store.items(scan_id=4, q='10')['total'], 0)
                self.assertTrue(all(c['all_modeled_fields_match'] for c in report['roundtrip_checks']))
            finally:
                store.close()
