from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.services.auction_importer import SnapshotValidationError, import_snapshot
from app.services.market_queries import item_listings, market_items, market_status


def _lua_snapshot(*, timestamp: int = 1_700_000_000, missing_core_count: int = 0) -> str:
    return f'''AuctionSearchDB = {{
["auctions"] = {{
  ["2026-08-20"] = {{
    ["scans"] = {{
      {{
        ["timestamp"] = {timestamp},
        ["itemCount"] = 5,
        ["recordCount"] = 5,
        ["linkedItemCount"] = 5,
        ["missingCoreCount"] = {missing_core_count},
        ["incompleteInfoCount"] = 0,
        ["apiErrorCount"] = 0,
        ["durationMs"] = 12.5,
        ["items"] = {{
          {{ ["itemID"] = 10, ["name"] = "测试矿石", ["quantity"] = 2,
             ["qualityID"] = 2, ["texture"] = 100, ["minBid"] = 0,
             ["buyoutAmount"] = 11, ["bidAmount"] = 0, ["timeLeftBand"] = 4,
             ["hasAllInfo"] = true, ["itemLink"] = "item:10" }},
          {{ ["itemID"] = 10, ["name"] = "测试矿石", ["quantity"] = 3,
             ["qualityID"] = 2, ["texture"] = 100, ["minBid"] = 0,
             ["buyoutAmount"] = 12, ["bidAmount"] = 0, ["timeLeftBand"] = 3,
             ["hasAllInfo"] = true, ["itemLink"] = "item:10" }},
          {{ ["itemID"] = 20, ["name"] = "仅竞价物品", ["quantity"] = 1,
             ["qualityID"] = 1, ["texture"] = 200, ["minBid"] = 50,
             ["buyoutAmount"] = 0, ["bidAmount"] = 0, ["timeLeftBand"] = 2,
             ["hasAllInfo"] = true, ["itemLink"] = "item:20" }},
          {{ ["itemID"] = 82800, ["battlePetCreatureID"] = 1001,
             ["battlePetDisplayID"] = 2001, ["name"] = "宠物甲", ["quantity"] = 1,
             ["qualityID"] = 3, ["texture"] = 301, ["minBid"] = 0,
             ["buyoutAmount"] = 101, ["bidAmount"] = 0, ["timeLeftBand"] = 3,
             ["hasAllInfo"] = true, ["itemLink"] = "battlepet:甲" }},
          {{ ["itemID"] = 82800, ["battlePetCreatureID"] = 1002,
             ["battlePetDisplayID"] = 2002, ["name"] = "宠物乙", ["quantity"] = 1,
             ["qualityID"] = 3, ["texture"] = 302, ["minBid"] = 0,
             ["buyoutAmount"] = 202, ["bidAmount"] = 0, ["timeLeftBand"] = 3,
             ["hasAllInfo"] = true, ["itemLink"] = "battlepet:乙" }}
        }}
      }}
    }}
  }}
}}
}}'''


class MarketPipelineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.engine = create_engine(f"sqlite:///{root / 'market.db'}")
        Base.metadata.create_all(self.engine)
        self.session_factory = sessionmaker(bind=self.engine)
        self.snapshot = root / "auction.lua"
        self.snapshot.write_text(_lua_snapshot(), encoding="utf-8")

    def tearDown(self):
        self.engine.dispose()
        self.temporary.cleanup()

    def test_import_dedupe_and_indexed_queries(self):
        imported = import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        self.assertEqual(imported.imported_listing_count, 5)
        self.assertEqual(imported.imported_scan_count, 1)

        duplicate = import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        self.assertTrue(duplicate.duplicate_snapshot)
        self.assertEqual(duplicate.imported_scan_count, 0)
        self.assertEqual(duplicate.imported_listing_count, 0)
        self.assertEqual(duplicate.existing_listing_count, 5)

        with self.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count(models.AuctionListing.id))), 5)
            self.assertEqual(db.scalar(select(func.count(models.AuctionItemSummary.id))), 4)
            status = market_status(db)
            self.assertEqual(status["listingCount"], 5)
            self.assertEqual(status["uniqueItemCount"], 3)
            self.assertEqual(status["marketItemCount"], 4)

            items = market_items(db, q="测试", page=1, page_size=20, sort="price_asc")
            self.assertEqual(items["total"], 1)
            self.assertEqual(items["items"][0]["listingCount"], 2)
            self.assertEqual(items["items"][0]["totalQuantity"], 5)
            self.assertEqual(items["items"][0]["minUnitPrice"], 4)

            listings = item_listings(db, item_id=10, battle_pet_creature_id=None, page=1, page_size=20)
            self.assertIsNotNone(listings)
            self.assertEqual(listings["total"], 2)
            self.assertEqual(listings["items"][0]["unitPrice"], 4)

            pets = market_items(db, q="82800", page=1, page_size=20, sort="name_asc")
            self.assertEqual(pets["total"], 2)
            self.assertEqual({item["marketKey"] for item in pets["items"]}, {"82800:1001", "82800:1002"})
            pet_details = item_listings(
                db, item_id=82800, battle_pet_creature_id=1001, page=1, page_size=20
            )
            self.assertEqual(pet_details["total"], 1)
            self.assertEqual(pet_details["name"], "宠物甲")

    def test_same_metadata_with_changed_content_is_a_distinct_scan(self):
        first = import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        changed = _lua_snapshot().replace('["buyoutAmount"] = 12', '["buyoutAmount"] = 13', 1)
        self.snapshot.write_text(changed, encoding="utf-8")
        second = import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        self.assertNotEqual(first.snapshot_sha256, second.snapshot_sha256)
        self.assertEqual(second.imported_scan_count, 1)
        with self.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count(models.AuctionScan.id))), 2)
            self.assertEqual(db.scalar(select(func.count(models.AuctionListing.id))), 10)

    def test_invalid_scan_is_not_partially_imported(self):
        self.snapshot.write_text(_lua_snapshot(missing_core_count=1), encoding="utf-8")
        with self.assertRaises(SnapshotValidationError):
            import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        with self.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count(models.AuctionSnapshot.id))), 0)
            self.assertEqual(db.scalar(select(func.count(models.AuctionScan.id))), 0)
            self.assertEqual(db.scalar(select(func.count(models.AuctionListing.id))), 0)

    def test_missing_item_link_is_rejected_even_if_metadata_claims_complete(self):
        incomplete = _lua_snapshot().replace('["itemLink"] = "item:20"', '["itemLink"] = ""', 1)
        self.snapshot.write_text(incomplete, encoding="utf-8")
        with self.assertRaises(SnapshotValidationError):
            import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        with self.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count(models.AuctionSnapshot.id))), 0)


if __name__ == "__main__":
    unittest.main()
