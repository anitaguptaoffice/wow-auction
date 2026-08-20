from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.services.auction_importer import SnapshotValidationError, import_snapshot
from app.services.market_queries import (
    _crafting_quality_from_item_link,
    _parse_battle_pet_variant,
    _parse_item_variant,
    item_history,
    item_listings,
    market_catalog,
    market_items,
    market_status,
)


def _lua_snapshot(
    *,
    timestamp: int = 1_700_000_000,
    missing_core_count: int = 0,
    realm_name: str = "测试服务器",
    realm_id: int = 123,
) -> str:
    return f'''AuctionSearchDB = {{
["auctions"] = {{
  ["2026-08-20"] = {{
    ["scans"] = {{
      {{
        ["timestamp"] = {timestamp},
        ["itemCount"] = 6,
        ["recordCount"] = 6,
        ["linkedItemCount"] = 6,
        ["missingCoreCount"] = {missing_core_count},
        ["incompleteInfoCount"] = 0,
        ["apiErrorCount"] = 0,
        ["durationMs"] = 12.5,
        ["realmName"] = "{realm_name}",
        ["normalizedRealmName"] = "{realm_name}",
        ["realmID"] = {realm_id},
        ["regionID"] = 5,
        ["regionName"] = "CN",
        ["itemMarketScopes"] = {{ [10] = "region", [20] = "realm", [82800] = "realm" }},
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
             ["hasAllInfo"] = true, ["itemLink"] = "|cnIQ3:|Hbattlepet:1180:25:3:1237:341:276:0000000000000000:47731|h[宠物甲]|h|r" }},
          {{ ["itemID"] = 82800, ["battlePetCreatureID"] = 1001,
             ["battlePetDisplayID"] = 2001, ["name"] = "宠物甲", ["quantity"] = 1,
             ["qualityID"] = 3, ["texture"] = 301, ["minBid"] = 0,
             ["buyoutAmount"] = 151, ["bidAmount"] = 0, ["timeLeftBand"] = 3,
             ["hasAllInfo"] = true, ["itemLink"] = "|cnIQ3:|Hbattlepet:1180:25:3:1237:305:305:0000000000000000:47731|h[宠物甲]|h|r" }},
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
        self.assertEqual(imported.imported_listing_count, 6)
        self.assertEqual(imported.imported_scan_count, 1)

        duplicate = import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        self.assertTrue(duplicate.duplicate_snapshot)
        self.assertEqual(duplicate.imported_scan_count, 0)
        self.assertEqual(duplicate.imported_listing_count, 0)
        self.assertEqual(duplicate.existing_listing_count, 6)

        with self.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count(models.AuctionListing.id))), 6)
            self.assertEqual(db.scalar(select(func.count(models.AuctionItemSummary.id))), 4)
            status = market_status(db)
            self.assertEqual(status["listingCount"], 6)
            self.assertEqual(status["uniqueItemCount"], 3)
            self.assertEqual(status["marketItemCount"], 5)
            self.assertEqual(status["realm"], "测试服务器")
            self.assertEqual(status["realmID"], 123)
            catalog = market_catalog(db)
            self.assertEqual(len(catalog["realms"]), 1)
            self.assertEqual(catalog["realms"][0]["realm"], "测试服务器")
            self.assertEqual(len(catalog["realms"][0]["scans"]), 1)

            items = market_items(db, q="测试", page=1, page_size=20, sort="price_asc")
            self.assertEqual(items["total"], 1)
            self.assertEqual(items["items"][0]["listingCount"], 2)
            self.assertEqual(items["items"][0]["totalQuantity"], 5)
            self.assertEqual(items["items"][0]["minUnitPrice"], 4)
            self.assertEqual(items["items"][0]["marketScope"], "region")

            listings = item_listings(db, item_id=10, battle_pet_creature_id=None, page=1, page_size=20)
            self.assertIsNotNone(listings)
            self.assertEqual(listings["total"], 2)
            self.assertEqual(listings["items"][0]["unitPrice"], 4)
            self.assertEqual(
                listings["displayColumns"],
                {"tier": False, "attributes": False, "petVariant": False},
            )

            pets = market_items(db, q="82800", page=1, page_size=20, sort="name_asc")
            self.assertEqual(pets["total"], 3)
            self.assertTrue(all(item["marketScope"] == "realm" for item in pets["items"]))
            pet_1001 = [item for item in pets["items"] if item["battlePetCreatureID"] == 1001]
            self.assertEqual({item["petBreedCode"] for item in pet_1001}, {"P/P", "P/S"})
            self.assertTrue(all(item["variantCount"] == 1 for item in pet_1001))
            high_power = next(item for item in pet_1001 if item["petBreedCode"] == "P/P")
            pet_details = item_listings(
                db,
                item_id=82800,
                battle_pet_creature_id=1001,
                pet_variant_key=high_power["petVariantKey"],
                page=1,
                page_size=20,
            )
            self.assertEqual(pet_details["total"], 1)
            self.assertEqual(pet_details["marketKey"], high_power["marketKey"])
            self.assertEqual(pet_details["name"], "宠物甲")
            self.assertEqual(
                pet_details["displayColumns"],
                {"tier": False, "attributes": False, "petVariant": True},
            )
            self.assertEqual(pet_details["items"][0]["petBreedCode"], "P/P")
            self.assertEqual(pet_details["items"][0]["petBreedLabel"], "高攻型")
            pet_history = item_history(
                db,
                item_id=82800,
                battle_pet_creature_id=1001,
                pet_variant_key=high_power["petVariantKey"],
            )
            self.assertEqual(pet_history["marketKey"], high_power["marketKey"])
            self.assertEqual(pet_history["points"][0]["listingCount"], 1)

    def test_battle_pet_breed_is_parsed_from_auction_link(self):
        high_power = _parse_battle_pet_variant(
            "|cnIQ3:|Hbattlepet:1180:25:3:1237:341:276:0000000000000000:47731"
            "|h[赞达拉袭胫者]|h|r"
        )
        self.assertEqual(high_power["petSpeciesID"], 1180)
        self.assertEqual(high_power["petLevel"], 25)
        self.assertEqual(high_power["petQualityLabel"], "精良")
        self.assertEqual(high_power["petBreedCode"], "P/P")
        self.assertEqual(high_power["petBreedLabel"], "高攻型")
        self.assertEqual(high_power["petBreedConfidence"], "exact")

        attack_speed = _parse_battle_pet_variant(
            "|cnIQ3:|Hbattlepet:1180:25:3:1237:305:305:0000000000000000:47731"
            "|h[赞达拉袭胫者]|h|r"
        )
        self.assertEqual(attack_speed["petBreedCode"], "P/S")
        self.assertEqual(attack_speed["petBreedLabel"], "攻速型")

    def test_raid_boe_variant_metadata_is_parsed_from_item_link(self):
        item_link = (
            "|cnIQ4:|Hitem:271438::::::::90:64::4:6:6652:13695:13662:13332:12825:10844:"
            "3:28:7360:29:49:30:40:::::|h[神殿探窟者秘法头盔]|h|r"
        )
        variant = _parse_item_variant(item_link)
        self.assertEqual(variant["difficulty"], "随机团队")
        self.assertEqual(variant["itemLevel"], 279)
        self.assertEqual(variant["upgradeTrack"], "老兵")
        self.assertEqual(variant["upgradeLevel"], "1/6")
        self.assertEqual(variant["statLabel"], "精通 > 全能")
        self.assertTrue(variant["hasSocket"])

    def test_crafting_quality_is_read_from_item_link_and_market_item(self):
        item_link = (
            "|cnIQ1:|Hitem:241304::::::::90:64:::::::::|h[银月城生命药水 "
            "|A:Professions-ChatIcon-Quality-12-Tier2:17:15::1|a]|h|r"
        )
        self.assertEqual(_crafting_quality_from_item_link(item_link), 2)
        legacy_item_link = (
            "|cnIQ3:|Hitem:190486::::::::90:64::13:::::::::|h[龙银大锤 "
            "|A:Professions-ChatIcon-Quality-Tier5:17:15::1|a]|h|r"
        )
        self.assertEqual(_crafting_quality_from_item_link(legacy_item_link), 5)

        crafted_snapshot = _lua_snapshot().replace('"item:10"', f'"{item_link}"')
        self.snapshot.write_text(crafted_snapshot, encoding="utf-8")
        import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        with self.session_factory() as db:
            result = market_items(db, q="测试矿石", page=1, page_size=20, sort="price_asc")
            self.assertEqual(result["items"][0]["craftingQuality"], 2)

    def test_raid_boe_collection_is_split_by_difficulty(self):
        raid_rows = '''
          { ["itemID"] = 271435, ["name"] = "嘶鸣密教便鞋", ["quantity"] = 1,
             ["qualityID"] = 4, ["texture"] = 7520892, ["minBid"] = 0,
             ["buyoutAmount"] = 900000000, ["bidAmount"] = 0, ["timeLeftBand"] = 4,
             ["hasAllInfo"] = true, ["itemLink"] = "|Hitem:271435::::::::90:64::4:1:13662:0:::::|h[嘶鸣密教便鞋]|h|r" },
          { ["itemID"] = 271435, ["name"] = "嘶鸣密教便鞋", ["quantity"] = 1,
             ["qualityID"] = 4, ["texture"] = 7520892, ["minBid"] = 0,
             ["buyoutAmount"] = 1900000000, ["bidAmount"] = 0, ["timeLeftBand"] = 4,
             ["hasAllInfo"] = true, ["itemLink"] = "|Hitem:271435::::::::90:64::5:1:13662:0:::::|h[嘶鸣密教便鞋]|h|r" },
          { ["itemID"] = 271444, ["name"] = "遗忘祭品肩铠", ["quantity"] = 1,
             ["qualityID"] = 4, ["texture"] = 7739391, ["minBid"] = 0,
             ["buyoutAmount"] = 1500000000, ["bidAmount"] = 0, ["timeLeftBand"] = 4,
             ["hasAllInfo"] = true, ["itemLink"] = "|Hitem:271444::::::::90:64::3:1:13662:0:::::|h[遗忘祭品肩铠]|h|r" },
'''
        snapshot = _lua_snapshot().replace('["itemCount"] = 6', '["itemCount"] = 9').replace(
            '["recordCount"] = 6', '["recordCount"] = 9'
        ).replace('["linkedItemCount"] = 6', '["linkedItemCount"] = 9').replace(
            '["items"] = {', f'["items"] = {{{raid_rows}', 1
        )
        self.snapshot.write_text(snapshot, encoding="utf-8")
        import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)

        with self.session_factory() as db:
            result = market_items(
                db,
                q=None,
                collection="raid_boe_12_1",
                page=1,
                page_size=20,
                sort="price_asc",
            )
            self.assertEqual(result["total"], 3)
            self.assertEqual(
                {(item["itemID"], item["itemContext"]) for item in result["items"]},
                {(271435, 4), (271435, 5), (271444, 3)},
            )
            self.assertEqual(result["items"][0]["difficulty"], "随机团队")

            lfr = item_listings(
                db,
                item_id=271435,
                battle_pet_creature_id=None,
                item_context=4,
                page=1,
                page_size=20,
            )
            self.assertEqual(lfr["total"], 1)
            self.assertEqual(lfr["items"][0]["difficulty"], "随机团队")
            self.assertEqual(
                lfr["displayColumns"],
                {"tier": True, "attributes": False, "petVariant": False},
            )

            history = item_history(
                db,
                item_id=271435,
                battle_pet_creature_id=None,
                item_context=5,
            )
            self.assertEqual(history["difficulty"], "英雄")
            self.assertEqual(history["points"][0]["listingCount"], 1)

    def test_same_metadata_with_changed_content_is_a_distinct_scan(self):
        first = import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        changed = _lua_snapshot().replace('["buyoutAmount"] = 12', '["buyoutAmount"] = 13', 1)
        self.snapshot.write_text(changed, encoding="utf-8")
        second = import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        self.assertNotEqual(first.snapshot_sha256, second.snapshot_sha256)
        self.assertEqual(second.imported_scan_count, 1)
        with self.session_factory() as db:
            self.assertEqual(db.scalar(select(func.count(models.AuctionScan.id))), 2)
            self.assertEqual(db.scalar(select(func.count(models.AuctionListing.id))), 12)

    def test_item_history_returns_one_aggregate_point_per_scan(self):
        import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        changed = _lua_snapshot(timestamp=1_700_003_600).replace(
            '["buyoutAmount"] = 11', '["buyoutAmount"] = 21', 1
        ).replace('["buyoutAmount"] = 12', '["buyoutAmount"] = 18', 1)
        self.snapshot.write_text(changed, encoding="utf-8")
        import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)

        with self.session_factory() as db:
            scan_ids = list(db.scalars(select(models.AuctionScan.id).order_by(models.AuctionScan.scanned_at_unix)))
            history = item_history(db, item_id=10, battle_pet_creature_id=None)
            self.assertIsNotNone(history)
            self.assertEqual(history["pointCount"], 2)
            self.assertEqual([point["minUnitPrice"] for point in history["points"]], [4, 6])
            self.assertEqual(history["change"]["minUnitPrice"]["absolute"], 2)
            self.assertEqual(history["change"]["minUnitPrice"]["percent"], 50.0)
            earlier = item_history(
                db, item_id=10, battle_pet_creature_id=None, scan_id=scan_ids[0]
            )
            self.assertEqual(earlier["pointCount"], 1)
            earlier_items = market_items(
                db, q="10", page=1, page_size=20, sort="price_asc", scan_id=scan_ids[0]
            )
            self.assertEqual(earlier_items["items"][0]["minUnitPrice"], 4)
            self.assertIsNone(item_history(db, item_id=999, battle_pet_creature_id=None))

    def test_unified_market_deduplicates_region_items_and_splits_realm_items(self):
        import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)
        other_realm = _lua_snapshot(
            timestamp=1_700_003_600,
            realm_name="第二服务器",
            realm_id=456,
        ).replace('["buyoutAmount"] = 11', '["buyoutAmount"] = 21', 1).replace(
            '["buyoutAmount"] = 101', '["buyoutAmount"] = 51', 1
        )
        self.snapshot.write_text(other_realm, encoding="utf-8")
        import_snapshot(self.snapshot, db_engine=self.engine, chunk_size=100)

        with self.session_factory() as db:
            shared = market_items(db, q="测试矿石", page=1, page_size=20, sort="price_asc")
            self.assertEqual(shared["total"], 1)
            self.assertEqual(shared["items"][0]["marketScope"], "region")
            self.assertEqual(shared["items"][0]["realmID"], 456)

            realm_only = market_items(db, q="仅竞价物品", page=1, page_size=20, sort="price_asc")
            self.assertEqual(realm_only["total"], 2)
            self.assertEqual({item["realmID"] for item in realm_only["items"]}, {123, 456})

            cross_realm_prices = market_items(db, q="82800", page=1, page_size=20, sort="price_asc")
            prices = [item["minUnitPrice"] for item in cross_realm_prices["items"]]
            self.assertEqual(prices, sorted(prices))
            self.assertEqual(cross_realm_prices["items"][0]["realmID"], 456)

            realm_history = item_history(db, item_id=20, battle_pet_creature_id=None)
            self.assertEqual(realm_history["marketScope"], "realm")
            self.assertEqual({point["realmID"] for point in realm_history["points"]}, {123, 456})

            shared_history = item_history(db, item_id=10, battle_pet_creature_id=None)
            self.assertEqual(shared_history["marketScope"], "region")
            self.assertEqual([point["scannedAtUnix"] for point in shared_history["points"]], sorted(
                point["scannedAtUnix"] for point in shared_history["points"]
            ))

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
