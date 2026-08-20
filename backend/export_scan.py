"""Rebuild one validated addon snapshot from the local indexed database."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def lua_value(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value), ensure_ascii=False)


def write_field(output, key: str, value) -> None:
    encoded = lua_value(value)
    if encoded is not None:
        output.write(f"[{json.dumps(key)}] = {encoded},\n")


def export_scan(database: Path, scan_id: int, destination: Path) -> None:
    db = sqlite3.connect(database)
    db.row_factory = sqlite3.Row
    scan = db.execute(
        """SELECT s.*, c.realm_name, c.normalized_realm_name, c.realm_id,
                         c.region_id, c.region_name
             FROM wow_auction_scans s
             JOIN wow_auction_scan_contexts c ON c.scan_id = s.id
            WHERE s.id = ? AND s.complete = 1""",
        (scan_id,),
    ).fetchone()
    if scan is None:
        raise SystemExit(f"complete scan {scan_id} not found")

    fields = {
        "timestamp": scan["scanned_at_unix"],
        "itemCount": scan["declared_item_count"],
        "recordCount": scan["record_count"],
        "linkedItemCount": scan["linked_item_count"],
        "missingCoreCount": scan["missing_core_count"],
        "incompleteInfoCount": scan["incomplete_info_count"],
        "apiErrorCount": scan["api_error_count"],
        "durationMs": scan["duration_ms"],
        "realmName": scan["realm_name"],
        "normalizedRealmName": scan["normalized_realm_name"],
        "realmID": scan["realm_id"],
        "regionID": scan["region_id"],
        "regionName": scan["region_name"],
    }
    listing_fields = {
        "name": "name",
        "texture": "texture",
        "quantity": "quantity",
        "qualityID": "quality_id",
        "usable": "usable",
        "level": "level",
        "levelType": "level_type",
        "minBid": "min_bid",
        "minIncrement": "min_increment",
        "buyoutAmount": "buyout_amount",
        "bidAmount": "bid_amount",
        "highBidder": "high_bidder",
        "bidderFullName": "bidder_full_name",
        "owner": "owner",
        "ownerFullName": "owner_full_name",
        "saleStatus": "sale_status",
        "hasAllInfo": "has_all_info",
        "itemID": "item_id",
        "itemLink": "item_link",
        "timeLeftBand": "time_left_band",
        "battlePetCreatureID": "battle_pet_creature_id",
        "battlePetDisplayID": "battle_pet_display_id",
    }

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        output.write('AuctionSearchDB = {\n["lastScanTime"] = ')
        output.write(str(scan["scanned_at_unix"]))
        output.write(',\n["auctions"] = {\n')
        output.write(f'[{json.dumps(scan["source_date"], ensure_ascii=False)}] = {{\n["scans"] = {{\n{{\n')
        for key, value in fields.items():
            write_field(output, key, value)
        output.write('["items"] = {\n')
        cursor = db.execute(
            "SELECT * FROM wow_auction_listings WHERE scan_id = ? ORDER BY source_index",
            (scan_id,),
        )
        count = 0
        for row in cursor:
            output.write("{\n")
            for lua_key, column in listing_fields.items():
                value = row[column]
                if column in {"usable", "high_bidder", "has_all_info"} and value is not None:
                    value = bool(value)
                write_field(output, lua_key, value)
            output.write("},\n")
            count += 1
        if count != scan["record_count"]:
            raise RuntimeError(f"listing count mismatch: {count} != {scan['record_count']}")
        output.write('},\n["itemMarketScopes"] = {\n')
        for item_id, scope in db.execute(
            "SELECT item_id, market_scope FROM wow_auction_item_market_scopes WHERE scan_id = ? ORDER BY item_id",
            (scan_id,),
        ):
            output.write(f"[{item_id}] = {json.dumps(scope)},\n")
        output.write("},\n},\n},\n},\n},\n}\n")
    db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan_id", type=int)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--database", type=Path, default=Path("data/wow-auction.db"))
    args = parser.parse_args()
    export_scan(args.database, args.scan_id, args.destination)


if __name__ == "__main__":
    main()
