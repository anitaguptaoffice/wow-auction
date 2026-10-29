"""Correctness contracts for the isolated dictionary-encoded prototype."""

import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from compact_market.build import build
from compact_market.store import MarketStore
from tests.test_market_pipeline import _lua_snapshot


class CompactMarketTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        sources = []
        for name, timestamp, realm, realm_id in [
            ("old", 1700000000, "Realm A", 123),
            ("new", 1700086400, "Realm A", 123),
            ("other", 1700086500, "Realm B", 456),
        ]:
            source = cls.root / f"{name}.lua"
            source.write_text(_lua_snapshot(timestamp=timestamp, realm_name=realm, realm_id=realm_id))
            sources.append(source)
        cls.database = cls.root / "market.duckdb"
        with contextlib.redirect_stdout(io.StringIO()):
            cls.report = build(sources, cls.database)
        cls.store = MarketStore(cls.database)

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.temp.cleanup()

    def test_lossless_dictionary_encoding_across_scans(self):
        self.assertEqual(self.report["counts"]["listings"], 18)
        self.assertEqual(self.report["counts"]["descriptors"], 5)
        self.assertEqual(len(self.report["roundtrip_checks"]), 3)
        self.assertTrue(all(c["all_modeled_fields_match"] for c in self.report["roundtrip_checks"]))

    def test_substring_search_and_exact_id(self):
        for query in ("测试矿", "试矿", "矿", "10"):
            result = self.store.items(scan_id=2, q=query)
            self.assertEqual(result["total"], 1)
            row = result["items"][0]
            self.assertEqual(row["itemID"], 10)
            self.assertEqual(row["listingCount"], 2)
            self.assertEqual(row["totalQuantity"], 5)
            self.assertEqual(row["minUnitPrice"], 4)
        self.assertEqual(self.store.items(q="%' OR 1=1 --")["total"], 0)

    def test_pet_variants_remain_separate(self):
        result = self.store.items(scan_id=2, q="宠物甲")
        self.assertEqual(result["total"], 2)
        self.assertEqual(len({r["marketKey"] for r in result["items"]}), 2)
        for row in result["items"]:
            detail = self.store.listings(scan_id=2, market_key=row["marketKey"])
            self.assertEqual(detail["total"], 1)
            self.assertEqual(detail["items"][0]["battlePetCreatureID"], 1001)

    def test_public_page_contains_only_display_fields_and_provenance(self):
        row = self.store.items(scan_id=2, q='10')['items'][0]
        page = self.store.listings(scan_id=2, market_key=row['marketKey'], page_size=1, public_only=True)
        item = page['items'][0]
        self.assertEqual((page['total'], page['totalPages'], len(page['items'])), (2, 2, 1))
        self.assertEqual((item['scan_id'], item['itemID'], item['unitPrice'], item['quantity']), (2, 10, 4, 3))
        self.assertIn('source_index', item)
        self.assertIn('itemLink', item)
        self.assertNotIn('item_link', item)
        self.assertNotIn('owner_full_name', item)
        self.assertNotIn('unit_price', item)

    def test_history_isolates_realms_and_selected_time(self):
        key = self.store.items(scan_id=2, q="10")["items"][0]["marketKey"]
        self.assertEqual([p["scan_id"] for p in self.store.history(scan_id=2, market_key=key)["points"]], [1, 2])
        self.assertEqual([p["scan_id"] for p in self.store.history(scan_id=1, market_key=key)["points"]], [1])
        other = self.store.history(scan_id=3, market_key=key)
        self.assertEqual([p["scan_id"] for p in other["points"]], [3])
        self.assertEqual(other["realmID"], 456)

    def test_pagination_and_null_price_order(self):
        rows = self.store.items(scan_id=2, page_size=1)
        self.assertEqual(rows["totalPages"], 5)
        self.assertEqual(rows["items"][0]["itemID"], 10)
        last = self.store.items(scan_id=2, page_size=1, page=5)
        self.assertIsNone(last["items"][0]["minUnitPrice"])
        key = rows["items"][0]["marketKey"]
        detail = self.store.listings(scan_id=2, market_key=key, page=2, page_size=1)
        self.assertEqual(detail["items"][0]["unitPrice"], 6)
        with self.assertRaises(KeyError):
            self.store.items(scan_id=999)
        with self.assertRaises(ValueError):
            self.store.items(sort="invalid")

    def test_existing_generation_and_incomplete_source_rejected(self):
        with self.assertRaises(ValueError):
            build([], self.database)
        bad = self.root / "incomplete.lua"
        bad.write_text(_lua_snapshot(missing_core_count=1))
        output = self.root / "bad.duckdb"
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
            build([bad], output)
        self.assertFalse(output.exists())

    def test_person_dictionary_preserves_missing_empty_and_present_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "auction.lua"
            source.write_text(_lua_snapshot().replace(
                '["quantity"] = 2,',
                '["quantity"] = 2, ["owner"] = "Seller", '
                '["ownerFullName"] = "Seller-Realm", ["bidderFullName"] = "", '
                '["minIncrement"] = 10, ["usable"] = false, ["highBidder"] = true,',
                1,
            ))
            with contextlib.redirect_stdout(io.StringIO()):
                report = build([source], root / "market.duckdb")
            self.assertEqual(report["counts"]["people"], 4)
            store = MarketStore(root / "market.duckdb")
            try:
                key = store.items(q="10")["items"][0]["marketKey"]
                rows = store.listings(market_key=key)["items"]
                row = next(r for r in rows if r["source_index"] == 0)
                self.assertEqual(row["owner"], "Seller")
                self.assertEqual(row["owner_full_name"], "Seller-Realm")
                self.assertEqual(row["bidder_full_name"], "")
                self.assertEqual(row["min_increment"], 10)
                self.assertIs(row["usable"], False)
                self.assertIs(row["high_bidder"], True)
                self.assertIsNone(next(r for r in rows if r["source_index"] == 1)["owner"])
            finally:
                store.close()

    def test_api_queries_and_error_responses(self):
        from compact_market.api import app
        with patch.dict("os.environ", {"COMPACT_DATABASE": str(self.database)}):
            with TestClient(app, raise_server_exceptions=True) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/").status_code, 200)
                response = client.get("/api/market/items", params={"scan_id": 2, "q": "测试"})
                self.assertEqual(response.json()["total"], 1)
                self.assertIn("server-timing", response.headers)
                key = response.json()["items"][0]["marketKey"]
                self.assertEqual(client.get("/api/market/listings", params={"scan_id": 2, "market_key": key}).json()["total"], 2)
                self.assertEqual(client.get("/api/market/history", params={"scan_id": 2, "market_key": key}).json()["realmID"], 123)
                self.assertEqual(client.get("/api/market/items", params={"scan_id": 999}).status_code, 404)
                self.assertEqual(client.get("/api/market/items", params={"sort": "invalid"}).status_code, 422)
                self.assertEqual(client.get("/api/market/items", params={"page_size": 10000}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
