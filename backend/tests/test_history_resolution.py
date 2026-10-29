import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from compact_market.build import build
from compact_market.history import sample_points
from compact_market.unified import UnifiedMarketStore
from tests.test_market_pipeline import _lua_snapshot


class HistoryResolutionTest(unittest.TestCase):
    def test_bounded_sampling_retains_extrema_and_does_not_connect_across_absence(self):
        points = [dict(scanId=i, scannedAtUnix=i*60, minUnitPrice=100, state='observed') for i in range(10000)]
        points[123]['minUnitPrice'] = 1
        points[8765]['minUnitPrice'] = 99999
        points[4567].update(minUnitPrice=None, state='absent')
        sampled = sample_points(points, 100)
        self.assertLessEqual(len(sampled), 100)
        self.assertEqual(min(p['minUnitPrice'] for p in sampled if p['minUnitPrice'] is not None), 1)
        self.assertEqual(max(p['minUnitPrice'] for p in sampled if p['minUnitPrice'] is not None), 99999)
        after = next(p for p in sampled if p['scanId'] > 4567)
        self.assertTrue(after['breakBefore'])
        self.assertTrue(sample_points([points[0], points[100]], gap_seconds=120)[1]['breakBefore'])

    def test_complete_snapshot_absence_is_not_zero_price_or_continuous_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            sources = []
            for index in range(3):
                content = _lua_snapshot(timestamp=1700000000 + index*3600)
                if index == 1:
                    content = content.replace('["itemID"] = 10', '["itemID"] = 11').replace('[10] = "region"', '[11] = "region"').replace('item:10', 'item:11')
                source = root / f'{index}.lua'
                source.write_text(content, encoding='utf-8')
                sources.append(source)
            with contextlib.redirect_stdout(io.StringIO()):
                build(sources, root / 'history.duckdb')
            store = UnifiedMarketStore(root / 'history.duckdb')
            try:
                item = store.items(scan_id=6, q='10')['items'][0]
                result = store.public_history(scan_id=6, market_key=item['marketKey'])
                self.assertEqual([p['state'] for p in result['points']], ['observed', 'absent', 'observed'])
                self.assertIsNone(result['points'][1]['minUnitPrice'])
                self.assertTrue(result['points'][2]['breakBefore'])
                self.assertEqual(store.listings(scan_id=4, market_key=item['marketKey'])['total'],0)
                self.assertIsNone(result['change']['minUnitPrice'])
                self.assertEqual(store.public_history(scan_id=6, market_key=item['marketKey'], start=1700003600)['pointCount'], 2)
                with self.assertRaises(ValueError):
                    store.public_history(scan_id=6, market_key=item['marketKey'], start=2, end=1)
            finally:
                store.close()
