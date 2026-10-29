"""Immutable add/remove, repeat import, exact realm identity, and recovery contracts."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

import duckdb

from app.services.auction_importer import sha256_file
from compact_market.build import build, extract_source
from compact_market.manage import archive_source, inventory, stage_generation
from compact_market.unified import UnifiedMarketStore
from tests.test_market_pipeline import _lua_snapshot


class SnapshotManagementTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.first = self.source('first', 1700000000, 707, '白银之手')
        self.second = self.source('second', 1700086400, 707, '白银之手')
        self.other = self.source('other', 1700086401, 741, '死亡之翼')
        self.new = self.source('new', 1700172800, 838, '熊猫酒仙')
        self.base = self.root / 'base.duckdb'
        with contextlib.redirect_stdout(io.StringIO()):
            build([self.first, self.second, self.other], self.base)
        self.base_hash = sha256_file(self.base)

    def tearDown(self):
        self.temp.cleanup()

    def source(self, name, timestamp, realm_id, realm):
        source = self.root / f'{name}.lua'
        source.write_text(_lua_snapshot(timestamp=timestamp, realm_id=realm_id, realm_name=realm), encoding='utf-8')
        return source

    def stage(self, **params):
        target = self.root / f'next-{len(list(self.root.glob("next-*.duckdb")))}.duckdb'
        with contextlib.redirect_stdout(io.StringIO()):
            return stage_generation(self.base, target, **params)

    def test_add_and_keep_exact_old_scan_preserves_ids_and_history(self):
        new_white = self.source('white', 1700172900, 707, '白银之手')
        result = self.stage(sources=[self.new, new_white], keep_scans=[2], expected_scans=3)
        self.assertEqual(result['snapshot_count'], 3)
        self.assertEqual({s['scan_id'] for s in result['scans']}, {2, 4, 5})
        self.assertEqual({s['realm_id'] for s in result['scans']}, {707, 838})
        self.assertEqual([s['scan_id'] for s in result['removed_scans']], [1, 3])
        self.assertTrue(all(c['all_modeled_fields_match'] for c in result['roundtrip_checks']))
        self.assertEqual(sha256_file(self.base), self.base_hash)
        store = UnifiedMarketStore(Path(result['database']))
        try:
            self.assertEqual({r['realmID'] for r in store.catalog()['realms']}, {0, 707, 838})
            white = next(i for i in store.items()['items'] if i['itemID'] == 20 and i['realmID'] == 707)
            self.assertEqual(store.public_history(market_key=white['marketKey'], scan_id=white['scanId'])['pointCount'], 2)
        finally:
            store.close()

    def test_reimport_is_idempotent_and_dictionaries_do_not_grow(self):
        result = self.stage(sources=[self.first, self.new, self.new])
        self.assertEqual(result['snapshot_count'], 4)
        self.assertEqual(len(result['skipped']), 2)
        with duckdb.connect(result['database'], read_only=True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM descriptors').fetchone()[0], 5)
            self.assertEqual(db.execute('SELECT count(*) FROM people').fetchone()[0], 1)

    def test_delete_realm_and_reimport_removed_snapshot(self):
        removed = self.stage(remove_realms=['5:741'])
        self.assertEqual(removed['snapshot_count'], 2)
        with contextlib.redirect_stdout(io.StringIO()):
            restored = stage_generation(Path(removed['database']), self.root / 'restored.duckdb', sources=[self.other])
        self.assertEqual(restored['snapshot_count'], 3)
        self.assertEqual({s['realm_id'] for s in restored['scans']}, {707, 741})
        self.assertEqual(inventory(self.base)['snapshot_count'], 3)

    def test_invalid_removal_empty_result_and_output_collision_fail_safely(self):
        for params in [dict(remove_scans=[999]), dict(remove_realms=['5:999']),
                       dict(remove_realms=['5:0']), dict(keep_scans=[]),
                       dict(keep_scans=[1], remove_scans=[2]), dict(expected_scans=100)]:
            with self.assertRaises(ValueError):
                self.stage(**params)
        with self.assertRaises(ValueError):
            stage_generation(self.base, self.base)
        self.assertEqual(sha256_file(self.base), self.base_hash)

    def test_deleted_highest_id_is_never_reused_for_another_server(self):
        removed = self.stage(remove_scans=[3])
        with contextlib.redirect_stdout(io.StringIO()):
            result = stage_generation(Path(removed['database']), self.root / 'new-realm.duckdb', sources=[self.new])
        self.assertEqual({s['scan_id'] for s in result['scans']}, {1, 2, 4})
        self.assertEqual(next(s['realm_id'] for s in result['scans'] if s['scan_id'] == 4), 838)

    def test_invalid_source_and_failed_verification_never_publish(self):
        bad = self.root / 'bad.lua'
        bad.write_text('AuctionSearchDB = {}', encoding='utf-8')
        target = self.root / 'failed.duckdb'
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            stage_generation(self.base, target, sources=[bad])
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix('.report.json').exists())
        self.assertEqual(sha256_file(self.base), self.base_hash)

    def test_archive_is_stable_idempotent_and_manifest_is_checked(self):
        source_hash = sha256_file(self.new)
        archive = archive_source(self.new, self.root / 'archives')
        self.assertEqual(archive_source(self.new, self.root / 'archives'), archive)
        with tempfile.TemporaryDirectory() as temp:
            lua, manifest = extract_source(archive, Path(temp))
            self.assertEqual(sha256_file(lua), source_hash)
            self.assertEqual(manifest['snapshotSha256'], source_hash)
        manifest = json.loads((archive.parent / 'manifest.json').read_text(encoding='utf-8'))
        manifest['archiveSha256'] = '0' * 64
        (archive.parent / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(ValueError):
            extract_source(archive, Path(temp))
        self.assertEqual(sha256_file(self.new), source_hash)
