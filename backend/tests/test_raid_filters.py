"""Price, count, realm and pagination contracts for combined raid equipment filters."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from compact_market.build import build
from compact_market.unified import UnifiedMarketStore
from compact_market.variant_filters import VariantFilters
from app.services.market_queries import _parse_item_variant
from tests.test_market_pipeline import _lua_snapshot


def link(context, stats, bonuses):
    return (f'|Hitem:271435::::::::90:64::{context}:{len(bonuses)}:'
            + ':'.join(map(str, bonuses)) + f':2:29:{stats[0]}:30:{stats[1]}:::::|h[装绑测试]|h|r')


class RaidFiltersTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        sources = []
        variants = [(6, (49, 40), [13696]), (6, (49, 40), [13695]),
                    (6, (49, 40), [13696, 40]), (6, (32, 36), [13695, 40]),
                    (6, (49, 40), [13695, 41]), (5, (49, 40), [13695, 40])]
        for realm, realm_id, offset in [('Realm A', 123, 0), ('Realm B', 456, 100)]:
            rows = []
            for index, (tier, stats, bonuses) in enumerate(variants, 1):
                rows.append('''{ ["itemID"]=271435, ["name"]="装绑测试", ["quantity"]=%d,
                  ["qualityID"]=4, ["texture"]=7520892, ["minBid"]=0, ["buyoutAmount"]=%d,
                  ["bidAmount"]=0, ["timeLeftBand"]=4, ["hasAllInfo"]=true, ["itemLink"]="%s" },'''
                            % (index, (index * 10 + offset) * index, link(tier, stats, bonuses)))
            snapshot = _lua_snapshot(realm_name=realm, realm_id=realm_id)
            snapshot = snapshot.replace('["itemMarketScopes"] = {', '["itemMarketScopes"] = { [271435]="realm",')
            for key in ('itemCount', 'recordCount', 'linkedItemCount'):
                snapshot = snapshot.replace(f'["{key}"] = 6', f'["{key}"] = 12')
            snapshot = snapshot.replace('["items"] = {', '["items"] = {' + '\n'.join(rows), 1)
            source = root / f'{realm_id}.lua'
            source.write_text(snapshot, encoding='utf-8')
            sources.append(source)
        cls.database = root / 'raid.duckdb'
        with contextlib.redirect_stdout(io.StringIO()):
            build(sources, cls.database)
        cls.store = UnifiedMarketStore(cls.database, memory_limit='1GB')

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.temp.cleanup()

    def items(self, **kwargs):
        return self.store.items(collection='raid_boe_12_1', **kwargs)

    def test_prices_counts_and_detail_share_same_filters(self):
        result = self.items(difficulty='6', stats='49-40', socket='yes')
        self.assertEqual(result['total'], 2)
        a, b = result['items']
        self.assertEqual((a['realmID'], a['minUnitPrice'], a['listingCount'], a['totalQuantity']), (123, 20, 2, 7))
        self.assertEqual(b['minUnitPrice'], 120)
        detail = self.store.listings(scan_id=a['scanId'], market_key=a['marketKey'], **a['variantFilters'])
        self.assertEqual(detail['total'], 2)
        self.assertEqual([i['unitPrice'] for i in detail['items']], [20, 50])
        self.assertTrue(all(i['hasSocket'] and i['statKey'] == '49-40' for i in detail['items']))

    def test_secondary_stat_count_does_not_count_link_socket_or_tertiary_differences(self):
        mythic = self.items(difficulty='6')['items'][0]
        self.assertEqual(mythic['variantCount'], 5)
        self.assertEqual(mythic['statCombinationCount'], 2)
        selected = self.items(difficulty='6', stats='49-40', socket='yes')['items'][0]
        self.assertEqual(selected['variantCount'], 2)
        self.assertEqual(selected['statCombinationCount'], 1)
        plain = next(row for row in self.items()['items'] if row['scanId'] == mythic['scanId'] and row['marketKey'] == mythic['marketKey'])
        self.assertEqual(plain['statCombinationCount'], 2)

    def test_conditions_cannot_match_different_listings(self):
        self.assertEqual(self.items(difficulty='6', stats='49-40', socket='yes', tertiary='avoidance')['total'], 0)
        result = self.items(difficulty='5', stats='49-40', socket='yes', tertiary='avoidance')
        self.assertEqual(result['total'], 2)
        self.assertEqual(result['items'][0]['minUnitPrice'], 60)

    def test_grouped_market_filters_then_merges_before_sort_and_pagination(self):
        result = self.items(group_markets=True, sort='price_desc', page_size=1)
        self.assertEqual((result['total'], result['totalPages']), (2, 2))
        first = result['items'][0]
        self.assertEqual((first['realmID'], first['minUnitPrice']), (123, 60))
        self.assertEqual(first['marketGroup']['listingCount'], 2)
        second = self.items(group_markets=True, sort='price_desc', page=2, page_size=1)['items'][0]
        self.assertEqual((second['minUnitPrice'], second['marketGroup']['listingCount'], second['marketGroup']['totalQuantity']), (10, 10, 30))
        self.assertEqual(second['statCombinationCount'], 2)
        filtered = self.items(group_markets=True, difficulty='6', stats='49-40', socket='yes')
        self.assertEqual(filtered['total'], 1)
        item = filtered['items'][0]
        self.assertEqual((item['realmID'], item['minUnitPrice'], item['listingCount']), (123,20,2))
        self.assertEqual((item['marketGroup']['marketCount'], item['marketGroup']['listingCount'], item['marketGroup']['totalQuantity']), (2,4,14))
        self.assertEqual(item['statCombinationCount'], 1)
        self.assertEqual(self.items(group_markets=True, scan_id=item['scanId'], difficulty='6')['total'], 1)

    def test_comparison_and_history_use_the_same_equipment_conditions(self):
        item = self.items(difficulty='6', stats='49-40', socket='yes')['items'][0]
        comparison = self.store.compare(market_key=item['marketKey'], **item['variantFilters'])
        self.assertEqual([r['minUnitPrice'] for r in comparison['items']], [20, 120])
        self.assertEqual({r['realmID'] for r in comparison['items']}, {123, 456})
        history = self.store.public_history(scan_id=item['scanId'], market_key=item['marketKey'], **item['variantFilters'])
        self.assertEqual(history['points'][-1]['minUnitPrice'], item['minUnitPrice'])
        self.assertEqual(history['points'][-1]['listingCount'], item['listingCount'])

    def test_no_socket_tertiary_and_ordered_stats(self):
        a = self.items(difficulty='6', socket='no', tertiary='none')['items'][0]
        self.assertEqual((a['listingCount'], a['minUnitPrice']), (1, 10))
        a = self.items(difficulty='6', tertiary='any')['items'][0]
        self.assertEqual((a['listingCount'], a['minUnitPrice']), (3, 30))
        self.assertEqual(self.items(stats='40-49')['total'], 0)
        self.assertEqual(self.items(tertiary='speed')['total'], 0)
        with self.assertRaises(ValueError):
            self.items(stats='49-40 OR 1=1')
        with self.assertRaises(ValueError):
            self.store.items(socket='yes')

    def test_filtering_precedes_sorting_and_pagination(self):
        result = self.items(socket='yes', tertiary='leech', sort='price_desc', page_size=1)
        self.assertEqual((result['total'], result['totalPages']), (2, 2))
        self.assertEqual(result['items'][0]['minUnitPrice'], 150)
        row = result['items'][0]
        detail = self.store.listings(scan_id=row['scanId'], market_key=row['marketKey'], page=2, page_size=1, **row['variantFilters'])
        self.assertEqual((detail['total'], len(detail['items'])), (1, 0))

    def test_parser_and_unknown_are_not_negative_matches(self):
        for bonus, name in [(40, 'avoidance'), (41, 'leech'), (42, 'speed'), (43, 'indestructible')]:
            variant = _parse_item_variant(link(6, (49, 40), [13695, bonus]))
            self.assertEqual(variant['tertiaryStats'], [name])
            self.assertTrue(variant['hasSocket'])
        self.assertFalse(_parse_item_variant(link(6, (49, 40), [13696]))['hasSocket'])
        for raw in (None, 'item:271435', '|Hitem:271435::::::::90:64::6:8:13695|h[x]|h'):
            self.assertFalse(VariantFilters(socket='no').matches(_parse_item_variant(raw)))
            self.assertFalse(VariantFilters(tertiary='none').matches(_parse_item_variant(raw)))

    def test_merged_latest_listings_filter_before_global_pagination_and_keep_tiers(self):
        rows=self.items(group_markets=True, difficulty='6', stats='49-40', socket='yes')['items']
        self.assertEqual(len(rows),1)
        item=rows[0]
        pages=[self.store.listings(scan_id=item['scanId'],market_key=item['marketKey'],all_markets=True,
                page=page,page_size=2,public_only=True,**item['variantFilters']) for page in (1,2)]
        self.assertEqual([p['total'] for p in pages],[4,4])
        listings=[row for page in pages for row in page['items']]
        self.assertEqual([r['unitPrice'] for r in listings],[20,50,120,150])
        self.assertEqual([r['realmID'] for r in listings],[123,123,456,456])
        self.assertEqual(sum(r['quantity'] for r in listings),14)
        self.assertTrue(all(r['itemContext']==6 and r['hasSocket'] and r['statKey']=='49-40' for r in listings))
        self.assertEqual(len({(r['scanId'],r['source_index']) for r in listings}),4)
        tiers=self.items(group_markets=True)['items']
        self.assertEqual({r['itemContext'] for r in tiers},{5,6})
        self.assertTrue(all(r['marketGroup']['marketCount']==2 for r in tiers))

    def test_http_contract(self):
        from compact_market.api import app
        with patch.dict('os.environ', {'COMPACT_DATABASE': str(self.database), 'COMPACT_ENGINE': 'unified', 'COMPACT_MEMORY_LIMIT': '1GB'}):
            with TestClient(app) as client:
                result = client.get('/api/market/items', params=dict(collection='raid_boe_12_1', socket='yes', tertiary='leech'))
                self.assertEqual(result.status_code, 200)
                row = result.json()['items'][0]
                result = client.get('/api/market/listings', params=dict(scan_id=row['scanId'], market_key=row['marketKey'], **row['variantFilters']))
                self.assertEqual(result.status_code, 200)
                self.assertEqual(result.json()['total'], 1)
                self.assertEqual(client.get('/api/market/items', params=dict(collection='raid_boe_12_1', socket='maybe')).status_code, 422)
