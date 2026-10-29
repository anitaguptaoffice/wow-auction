"""Immutable hot summaries plus SQL access to compressed auction facts."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import threading
import time

import duckdb

from app.services.auction_labels import label_time_left_band
from app.services.market_queries import _parse_item_variant, _parse_battle_pet_variant, RAID_BOE_12_1_ITEM_IDS
from compact_market.variant_filters import VariantFilters
from compact_market.schema import RESTORE_SQL

SORTS = ("price_asc", "price_desc", "quantity_desc", "listings_desc", "name_asc")

# Public pages need neither person joins nor duplicate internal snake_case fields.
# LIMIT/OFFSET is applied to facts before joining and decoding item descriptors.
PUBLIC_LISTINGS_SQL = """
SELECT f.scan_id, f.source_index, d.item_id, d.name, d.texture, f.quantity,
       d.quality_id, f.buyout_amount, f.unit_price, f.min_bid, f.bid_amount,
       d.item_link, f.time_left_band, d.battle_pet_creature_id
FROM listings f JOIN descriptors d USING (descriptor_id)
"""


def iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat().replace("+00:00", "Z")


def rows_as_dicts(cursor) -> list[dict]:
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def grams(text: str) -> set[str]:
    return set(text) if len(text) == 1 else {text[i:i + 2] for i in range(len(text) - 1)}


class MarketStore:
    def __init__(self, database: Path, *, threads: int = 2, memory_limit: str = "1GB"):
        started = time.perf_counter()
        if not database.is_file():
            raise ValueError(f"Generation does not exist: {database}")
        self.database = database
        self.root = duckdb.connect(str(database), read_only=True,
                                   config={"threads": threads, "memory_limit": memory_limit})
        self.local = threading.local()
        self.connection_lock = threading.Lock()
        self.connections = []
        self.scans = {r["scan_id"]: r for r in rows_as_dicts(self.root.execute("SELECT * FROM scans"))}
        if not self.scans:
            raise ValueError("Generation contains no scans")
        self.default_scan = max(self.scans, key=lambda s: (self.scans[s]["timestamp"], s))
        self.records = rows_as_dicts(self.root.execute("""
            SELECT s.*, coalesce(scope.market_scope, 'unknown') AS market_scope
            FROM summaries s LEFT JOIN scopes scope ON scope.scan_id = s.scan_id
                 AND scope.item_id = s.item_id
            ORDER BY s.scan_id, s.market_key
        """))
        self.by_scan = defaultdict(list)
        self.search_index = defaultdict(set)
        self.item_index = defaultdict(set)
        self.names = []
        self.lookup = {}
        for index, record in enumerate(self.records):
            self.by_scan[record["scan_id"]].append(index)
            self.lookup[record["scan_id"], record["market_key"]] = record
            name = record["name"].casefold()
            self.names.append(name)
            # Both character and bigram postings preserve arbitrary substring search.
            for gram in set(name) | grams(name):
                self.search_index[gram].add(index)
            self.item_index[str(record["item_id"])].add(index)
        self.sorted_ids = {}
        for scan_id, ids in self.by_scan.items():
            for sort in SORTS:
                self.sorted_ids[scan_id, sort] = sorted(ids, key=lambda i: self._sort_key(self.records[i], sort))
        # API rows are computed once per generation; requests only select references.
        self.public_records = [self._public_record(record) for record in self.records]
        self.load_seconds = time.perf_counter() - started

    @staticmethod
    def _sort_key(row: dict, sort: str):
        if sort.startswith("price_"):
            price = row["min_unit_price"]
            return price is None, (price or 0) * (-1 if sort == "price_desc" else 1), row["market_key"]
        if sort == "quantity_desc":
            return -row["total_quantity"], row["market_key"]
        if sort == "listings_desc":
            return -row["listing_count"], row["market_key"]
        return row["name"], row["market_key"]

    def connection(self):
        if not hasattr(self.local, "connection"):
            with self.connection_lock:
                self.local.connection = self.root.cursor()
                self.connections.append(self.local.connection)
        return self.local.connection

    def close(self):
        for connection in self.connections:
            connection.close()
        self.root.close()

    def scan(self, scan_id: int | None):
        key = self.default_scan if scan_id is None else scan_id
        if key not in self.scans:
            raise KeyError("Scan not found")
        return self.scans[key]

    def _public_record(self, row: dict) -> dict:
        scan = self.scans[row["scan_id"]]
        return {
            "scanId": row["scan_id"], "scannedAt": iso(scan["timestamp"]),
            "scannedAtUnix": scan["timestamp"], "realm": scan["realm_name"],
            "realmID": scan["realm_id"], "region": scan["region_name"],
            "regionID": scan["region_id"], "marketKey": row["market_key"],
            "itemID": row["item_id"], "name": row["name"], "texture": row["texture"],
            "quality": row["quality_id"], "battlePetCreatureID": row["battle_pet_creature_id"],
            "iconName": getattr(self, 'icon_map', {}).get(str(row['texture'])),
            "itemContext": row["item_context"], "craftingQuality": row["crafting_quality"],
            "petVariantKey": row["pet_variant_key"] or None,
            "marketScope": row["market_scope"], "listingCount": row["listing_count"],
            "variantCount": row["variant_count"], "totalQuantity": row["total_quantity"],
            "minUnitPrice": row["min_unit_price"], "minBuyout": row["min_buyout"],
            **_parse_item_variant(row["representative_link"]),
            **(_parse_battle_pet_variant(row["representative_link"]) or {}),
        }

    def catalog(self) -> dict:
        realms = defaultdict(list)
        for scan in self.scans.values():
            realms[scan["region_id"], scan["realm_id"]].append(scan)
        result = []
        for (region, realm), scans in sorted(realms.items(), key=lambda x: str(x[0])):
            scans.sort(key=lambda s: (s["timestamp"], s["scan_id"]), reverse=True)
            result.append({
                "key": f"{region}:{realm}", "region": scans[0]["region_name"],
                "regionID": region, "realm": scans[0]["realm_name"], "realmID": realm,
                "latestScanId": scans[0]["scan_id"],
                "scans": [{"scanId": s["scan_id"], "scannedAt": iso(s["timestamp"]),
                           "scannedAtUnix": s["timestamp"], "listingCount": s["record_count"]}
                          for s in scans],
            })
        return {"realms": result}

    def status(self, scan_id: int | None = None) -> dict:
        scan = self.scan(scan_id)
        return {
            "available": True, "complete": True, "scanId": scan["scan_id"],
            "snapshotSha256": scan["snapshot_sha256"], "scannedAt": iso(scan["timestamp"]),
            "listingCount": scan["record_count"], "totalQuantity": scan["total_quantity"],
            "marketItemCount": len(self.by_scan[scan["scan_id"]]),
            "realm": scan["realm_name"], "realmID": scan["realm_id"],
            "region": scan["region_name"], "regionID": scan["region_id"],
            "engine": "compact-duckdb", "generation": self.database.name,
            "loadSeconds": round(self.load_seconds, 3),
        }

    def items(self, *, scan_id: int | None = None, q: str = "", sort: str = "price_asc",
              page: int = 1, page_size: int = 20) -> dict:
        scan = self.scan(scan_id)
        if sort not in SORTS or page < 1 or not 1 <= page_size <= 100:
            raise ValueError("Invalid sort or pagination")
        ordered = self.sorted_ids[scan["scan_id"], sort]
        text = q.strip().casefold()
        if text:
            postings = [self.search_index.get(g, set()) for g in grams(text)]
            matches = set.intersection(*sorted(postings, key=len)) if postings else set()
            matches = {i for i in matches if text in self.names[i]}
            matches.update(self.item_index.get(text, set()))
            ordered = [i for i in ordered if i in matches]
        total = len(ordered)
        start = (page - 1) * page_size
        return {
            "scanId": scan["scan_id"], "page": page, "pageSize": page_size,
            "total": total, "totalPages": (total + page_size - 1) // page_size,
            "items": [self.public_records[i] for i in ordered[start:start + page_size]],
        }

    def require_market(self, scan_id: int | None, market_key: str) -> tuple[dict, dict]:
        scan = self.scan(scan_id)
        row = self.lookup.get((scan["scan_id"], market_key))
        if row is None:
            raise KeyError("Market item not found in selected scan")
        return scan, row

    def listings(self, *, market_key: str, scan_id: int | None = None,
                 page: int = 1, page_size: int = 50,
                 difficulty='', stats='', socket='', tertiary='', public_only=False) -> dict:
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("Invalid pagination")
        scan, summary = self.require_market(scan_id, market_key)
        filters = VariantFilters(difficulty, stats, socket, tertiary)
        descriptor_condition = ''
        descriptor_params = []
        if filters.active:
            if summary['item_id'] not in RAID_BOE_12_1_ITEM_IDS:
                raise ValueError('Equipment filters require a raid item')
            descriptors = rows_as_dicts(self.connection().execute(
                'SELECT descriptor_id, item_link FROM descriptors WHERE market_key = ?', [market_key]))
            descriptor_params = [d['descriptor_id'] for d in descriptors if filters.matches(_parse_item_variant(d['item_link']))]
            descriptor_condition = ' AND descriptor_id IN (' + ','.join('?' for _ in descriptor_params) + ')' if descriptor_params else ' AND false'
        selected = """(SELECT * FROM listings WHERE scan_id = ?
            AND descriptor_id IN (SELECT descriptor_id FROM descriptors WHERE market_key = ?)
            """ + descriptor_condition + """
            ORDER BY unit_price ASC NULLS LAST, source_index ASC LIMIT ? OFFSET ?)"""
        query = (PUBLIC_LISTINGS_SQL if public_only else RESTORE_SQL).replace("FROM listings f", "FROM " + selected + " f")
        cursor = self.connection().execute(query + " ORDER BY f.unit_price ASC NULLS LAST, f.source_index",
            [scan.get("source_scan_id", scan["scan_id"]), market_key, *descriptor_params, page_size, (page - 1) * page_size])
        items = []
        for row in rows_as_dicts(cursor):
            items.append(self._public_listing(row, public_only=public_only))
        total = summary["listing_count"]
        if filters.active:
            total = self.connection().execute('''SELECT count(*) FROM listings WHERE scan_id = ?
                AND descriptor_id IN (SELECT descriptor_id FROM descriptors WHERE market_key = ?)'''
                + descriptor_condition, [scan.get('source_scan_id', scan['scan_id']), market_key, *descriptor_params]).fetchone()[0]
        return {**self._public_record(summary),
                "displayColumns": self._listing_columns(items),
                "page": page, "pageSize": page_size, "total": total,
                "totalPages": (total + page_size - 1) // page_size, "items": items}

    @staticmethod
    def _listing_columns(items):
        return {
            "tier": any(i.get('itemLevel') or i.get('difficulty') or i.get('upgradeTrack') for i in items),
            "attributes": any(i.get('statLabel') or i.get('hasSocket') or i.get('tertiaryStats') for i in items),
            "petVariant": any(i.get('petSpeciesID') for i in items),
        }

    @staticmethod
    def _public_listing(row, *, public_only=False):
        return {
                **({} if public_only else row),
                "scan_id": row["scan_id"], "source_index": row["source_index"],
                "itemID": row["item_id"], "quality": row["quality_id"],
                "name": row["name"], "texture": row["texture"], "quantity": row["quantity"],
                "unitPrice": row["unit_price"], "buyoutAmount": row["buyout_amount"],
                "minBid": row["min_bid"], "bidAmount": row["bid_amount"],
                "itemLink": row["item_link"], "timeLeftBand": row["time_left_band"],
                "timeLeftLabel": label_time_left_band(row["time_left_band"]),
                "battlePetCreatureID": row["battle_pet_creature_id"],
                **_parse_item_variant(row["item_link"]),
                **(_parse_battle_pet_variant(row["item_link"]) or {}),
            }

    def history(self, *, market_key: str, scan_id: int | None = None) -> dict:
        scan, _ = self.require_market(scan_id, market_key)
        # Equality on region/realm deliberately prevents cross-realm history mixing.
        rows = rows_as_dicts(self.connection().execute("""
            SELECT s.scan_id, s.timestamp, s.realm_id, s.region_id,
                   m.min_unit_price, m.total_quantity, m.listing_count
            FROM summaries m JOIN scans s USING(scan_id)
            WHERE m.market_key = ? AND s.region_id IS NOT DISTINCT FROM ?
              AND s.realm_id IS NOT DISTINCT FROM ? AND s.timestamp <= ?
            ORDER BY s.timestamp, s.scan_id
        """, [market_key, scan["region_id"], scan["realm_id"], scan["timestamp"]]))
        return {"marketKey": market_key, "realmID": scan["realm_id"],
                "regionID": scan["region_id"], "points": rows}
