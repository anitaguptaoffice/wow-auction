"""Connected markets share latest observations, histories and retention floors."""
import contextlib
import copy
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import duckdb

from compact_market.auction_markets import Registry, bundled_registry, load_registry
from compact_market.build import build
from compact_market.manage import inventory, stage_generation
from compact_market.retention import plan
from compact_market.unified import UnifiedMarketStore
from compact_market.update import accept
from deploy.dataset import verify_catalog
from tests.test_market_pipeline import _lua_snapshot


class RegistryTest(unittest.TestCase):
    def test_evidence_intervals_regions_and_unknowns(self):
        registry = bundled_registry()
        start = registry.document['groups'][0]['valid_from']
        source = dict(region_id=5, realm_id=1794, realm_name='凤凰之神', timestamp=start)
        connected = registry.resolve(source)
        self.assertEqual(connected['auction_realm_id'], 1515)
        self.assertEqual(len(connected['aliases']), 2)
        self.assertTrue(connected['evidence'])
        self.assertNotEqual(registry.resolve(source, timestamp=start-1)['auction_market_id'], connected['auction_market_id'])
        self.assertEqual(registry.resolve(dict(source, region_id=1))['auction_realm_id'], 1794)
        self.assertEqual(registry.resolve(dict(source, realm_id=999))['auction_realm_id'], 999)
        document = copy.deepcopy(registry.document)
        document['groups'][0]['valid_to'] = start+100
        split = Registry(document).resolve(source, timestamp=start+100)
        self.assertNotEqual(split['auction_market_id'], registry.resolve(source, timestamp=start-1)['auction_market_id'])

    def test_ambiguous_or_unevidenced_registry_is_rejected(self):
        document = copy.deepcopy(bundled_registry().document)
        duplicate = copy.deepcopy(document['groups'][0])
        duplicate['auction_market_id'] += '-other'
        document['groups'].append(duplicate)
        with self.assertRaisesRegex(ValueError, 'Overlapping'): Registry(document)
        document = copy.deepcopy(bundled_registry().document)
        document['groups'][0]['evidence'] = []
        with self.assertRaisesRegex(ValueError, 'evidence'): Registry(document)

    def test_legacy_database_never_silently_adopts_new_mapping(self):
        with duckdb.connect() as db:
            self.assertEqual(load_registry(db).document['groups'], [])
            registry = bundled_registry()
            registry.freeze(db)
            self.assertEqual(load_registry(db).sha256, registry.sha256)


class ConnectedGenerationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.now = 1793361600
        sources = []
        for index, realm in enumerate((1794, 1515, 707)):
            path = self.root / ('%d.lua' % realm)
            path.write_text(_lua_snapshot(timestamp=self.now-300+index*100, realm_id=realm, realm_name=str(realm)))
            sources.append(path)
        self.base = self.root/'market.duckdb'
        with contextlib.redirect_stdout(io.StringIO()): build(sources, self.base)

    def tearDown(self):
        self.temp.cleanup()

    def test_latest_history_listings_aliases_and_catalog_verifier(self):
        scans = inventory(self.base)['scans']
        store = UnifiedMarketStore(self.base)
        try:
            catalog = store.catalog()
            verify_catalog(catalog, scans)
            connected = next(r for r in catalog['realms'] if r['realmID'] == 1515)
            self.assertEqual({a['realmID'] for a in connected['aliases']}, {1794,1515})
            self.assertEqual(len(connected['scans']), 2)
            self.assertEqual(len(store.latest_scans), 3)  # region + two auction markets
            sid = connected['latestScanId']
            latest = store.items(scan_id=sid)['items']
            key = next(r['marketKey'] for r in latest if r['itemID'] == 20)
            merged = next(r for r in store.items(group_markets=True)['items'] if r['marketKey'] == key)
            self.assertEqual(merged['marketGroup']['listingCount'], 2)  # pair + independent
            self.assertEqual(merged['marketGroup']['marketCount'], 2)
            detail = store.listings(scan_id=sid, market_key=key, all_markets=True)
            self.assertEqual(detail['total'], 2)
            history = store.history(scan_id=sid, market_key=key)
            self.assertEqual([p['source_realm_id'] for p in history['points']], [1794,1515])
            public = store.public_history(scan_id=sid, market_key=key, history_realm_id=1794)
            self.assertEqual(public['pointCount'], 2)
            self.assertEqual({p['sourceRealmID'] for p in public['points']}, {1794,1515})
            self.assertEqual(len(store.compare(scan_id=sid, market_key=key)['items']), 2)
        finally: store.close()

    def test_cap_uses_one_floor_for_connected_members_without_daily_downsampling(self):
        scans = inventory(self.base)['scans']
        default = plan(scans, {}, self.now)
        self.assertEqual(default['remove_scan_ids'], [])
        pressure = plan(scans, {'max_snapshots':2}, self.now)
        self.assertEqual(len(pressure['protected_scan_ids']), 2)
        self.assertEqual(pressure['remove_scan_ids'], [scans[0]['scan_id']])
        over = plan(scans, {'max_snapshots':1}, self.now)
        self.assertEqual(over['after_count'], 2)
        self.assertEqual(over['cap_exceeded_by'], 1)

    def test_registry_is_frozen_and_explicit_migration_preserves_sources(self):
        empty = dict(schema_version=1,version='independent-test',groups=[])
        output = self.root/'independent.duckdb'
        report = stage_generation(self.base, output, market_registry=empty)
        accept(output, report)
        store = UnifiedMarketStore(output)
        try:
            self.assertEqual(len(store.latest_scans), 4)
        finally: store.close()
        with patch('compact_market.auction_markets.bundled_registry', side_effect=AssertionError('must not reload defaults')):
            preserved = stage_generation(output, self.root/'preserved.duckdb')
            self.assertEqual(preserved['market_registry_version'], 'independent-test')
        restored = stage_generation(output, self.root/'connected.duckdb', market_registry=bundled_registry().document)
        self.assertEqual(restored['snapshot_count'], 3)
        self.assertEqual({s['realm_id'] for s in restored['scans']}, {1794,1515,707})
        accept(self.root/'connected.duckdb', restored)

    def test_pre_connection_history_is_separate_and_not_added_to_latest(self):
        document = copy.deepcopy(bundled_registry().document)
        document['groups'][0]['valid_from'] = self.now-250
        output = self.root/'boundary.duckdb'
        stage_generation(self.base, output, market_registry=document)
        store = UnifiedMarketStore(output)
        try:
            connected = next(r for r in store.catalog()['realms'] if r['realmID'] == 1515)
            historical = next(r for r in store.catalog()['realms'] if r['realmID'] == 1794)
            self.assertTrue(historical['archived'])
            self.assertNotIn(historical['latestScanId'], store.latest_scans)
            key = next(r['marketKey'] for r in store.items(scan_id=connected['latestScanId'])['items'] if r['itemID']==20)
            self.assertEqual(len(store.history(scan_id=connected['latestScanId'], market_key=key)['points']), 1)
            self.assertEqual(len(store.history(scan_id=historical['latestScanId'], market_key=key)['points']), 1)
        finally: store.close()


if __name__ == '__main__':
    unittest.main()
