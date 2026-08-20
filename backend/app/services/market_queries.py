"""Indexed SQL queries for the public market API."""

from __future__ import annotations

import math
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.services.auction_labels import label_time_left_band

SUPPORTED_SORTS = {"price_asc", "price_desc", "quantity_desc", "listings_desc", "name_asc"}
RAID_BOE_12_1_ITEM_IDS = frozenset(
    {271434, 271435, 271436, 271438, 271440, 271441, 271444, 271445, 271638}
)
SUPPORTED_COLLECTIONS = {"raid_boe_12_1": RAID_BOE_12_1_ITEM_IDS}

ITEM_CONTEXT_LABELS = {
    3: "普通",
    4: "随机团队",
    5: "英雄",
    6: "史诗",
}
ITEM_CONTEXT_ORDER = {4: 0, 3: 1, 5: 2, 6: 3}
SECONDARY_STAT_LABELS = {
    32: "暴击",
    36: "急速",
    40: "全能",
    49: "精通",
}
KNOWN_ITEM_LEVEL_BONUSES = {
    13662: 279,
}
_CRAFTING_QUALITY_RE = re.compile(r"Professions-ChatIcon-Quality-(?:\d+-)?Tier(\d+)")
_BATTLE_PET_LINK_RE = re.compile(
    r"(?:\|H)?battlepet:(\d+):(\d+):(\d+):(\d+):(\d+):(\d+):([^:]*):(\d+)"
)
PET_QUALITY_LABELS = {
    0: "粗糙",
    1: "普通",
    2: "优秀",
    3: "精良",
    4: "史诗",
    5: "传说",
}
PET_QUALITY_MULTIPLIERS = {
    0: 0.5,
    1: 0.550000011920929,
    2: 0.600000023841858,
    3: 0.649999976158142,
    4: 0.699999988079071,
    5: 0.75,
}
PET_BREEDS = {
    3: ("B/B", "均衡型", (0.5, 0.5, 0.5)),
    4: ("P/P", "高攻型", (0.0, 2.0, 0.0)),
    5: ("S/S", "高速型", (0.0, 0.0, 2.0)),
    6: ("H/H", "高血型", (2.0, 0.0, 0.0)),
    7: ("H/P", "血攻型", (0.9, 0.9, 0.0)),
    8: ("P/S", "攻速型", (0.0, 0.9, 0.9)),
    9: ("H/S", "血速型", (0.9, 0.0, 0.9)),
    10: ("P/B", "攻击均衡型", (0.4, 0.9, 0.4)),
    11: ("S/B", "速度均衡型", (0.4, 0.4, 0.9)),
    12: ("H/B", "生命均衡型", (0.9, 0.4, 0.4)),
}


def _load_pet_breed_species() -> dict[str, dict[str, Any]]:
    path = Path(__file__).resolve().parents[1] / "pet-breed-data.json"
    with path.open(encoding="utf-8") as source:
        return json.load(source)["species"]


PET_BREED_SPECIES = _load_pet_breed_species()


def _crafting_quality_from_item_link(item_link: str | None) -> int | None:
    if not item_link:
        return None
    match = _CRAFTING_QUALITY_RE.search(item_link)
    return int(match.group(1)) if match else None


def _pet_breed_diff(
    base_stats: list[float],
    breed_id: int,
    quality_multiplier: float,
    level: int,
    health: int,
    power: int,
    speed: int,
) -> float:
    breed_stats = PET_BREEDS[breed_id][2]
    quality_level = quality_multiplier * 20 * level
    expected = (
        (base_stats[0] * 10 + breed_stats[0] * 10) * quality_level * 5 + 10_000,
        (base_stats[1] * 10 + breed_stats[1] * 10) * quality_level,
        (base_stats[2] * 10 + breed_stats[2] * 10) * quality_level,
    )
    if level <= 2:
        expected = tuple(math.floor(value * 0.01 + 0.5) / 0.01 for value in expected)
    return (
        abs(expected[0] - health * 100) / 5
        + abs(expected[1] - power * 100)
        + abs(expected[2] - speed * 100)
    )


def _parse_battle_pet_variant(item_link: str | None) -> dict[str, Any] | None:
    if not item_link:
        return None
    match = _BATTLE_PET_LINK_RE.search(item_link)
    if not match:
        return None
    species_id, level, quality, health, power, speed, _, display_id = match.groups()
    parsed = {
        "petSpeciesID": int(species_id),
        "petLevel": int(level),
        "petQuality": int(quality),
        "petQualityLabel": PET_QUALITY_LABELS.get(int(quality), f"品质 {quality}"),
        "petHealth": int(health),
        "petPower": int(power),
        "petSpeed": int(speed),
        "petDisplayID": int(display_id),
        "petBreedCode": None,
        "petBreedLabel": None,
        "petBreedConfidence": "unknown",
    }
    species = PET_BREED_SPECIES.get(species_id)
    quality_multiplier = PET_QUALITY_MULTIPLIERS.get(int(quality))
    if not species or quality_multiplier is None or int(level) <= 0:
        return parsed

    candidate_ids = species.get("breeds") or list(PET_BREEDS)
    diffs = {
        breed_id: _pet_breed_diff(
            species["stats"],
            breed_id,
            quality_multiplier,
            int(level),
            int(health),
            int(power),
            int(speed),
        )
        for breed_id in candidate_ids
        if breed_id in PET_BREEDS
    }
    if not diffs:
        return parsed
    smallest = min(diffs.values())
    matches = [
        breed_id for breed_id, diff in diffs.items()
        if math.isclose(diff, smallest, rel_tol=0, abs_tol=1e-6)
    ]
    codes = [PET_BREEDS[breed_id][0] for breed_id in matches]
    parsed["petBreedCode"] = " / ".join(codes)
    if len(matches) == 1:
        parsed["petBreedLabel"] = PET_BREEDS[matches[0]][1]
        parsed["petBreedConfidence"] = "exact"
    else:
        parsed["petBreedLabel"] = "低等级暂不可唯一判定"
        parsed["petBreedConfidence"] = "ambiguous"
    return parsed


def _pet_variant_key(pet_variant: dict[str, Any] | None) -> str:
    """Return a stable key for one auctionable battle-pet stat variant."""
    if pet_variant is None:
        return "unknown"
    return ":".join(
        str(pet_variant[field])
        for field in (
            "petSpeciesID",
            "petLevel",
            "petQuality",
            "petHealth",
            "petPower",
            "petSpeed",
        )
    )


def _pet_market_key(item_id: int, battle_pet_creature_id: int | None, pet_variant_key: str) -> str:
    return f"{item_id}:{battle_pet_creature_id or 0}:pet:{pet_variant_key}"


def _pet_variant_defaults() -> dict[str, Any]:
    return {
        "petSpeciesID": None,
        "petLevel": None,
        "petQuality": None,
        "petQualityLabel": None,
        "petHealth": None,
        "petPower": None,
        "petSpeed": None,
        "petDisplayID": None,
        "petBreedCode": None,
        "petBreedLabel": None,
        "petBreedConfidence": None,
    }


def latest_complete_scan(db: Session) -> models.AuctionScan | None:
    return db.scalar(
        select(models.AuctionScan)
        .where(models.AuctionScan.complete.is_(True))
        .order_by(models.AuctionScan.scanned_at_unix.desc(), models.AuctionScan.id.desc())
        .limit(1)
    )


def latest_complete_scans_by_realm(db: Session) -> list[tuple[models.AuctionScan, models.AuctionScanContext]]:
    rows = db.execute(
        select(models.AuctionScan, models.AuctionScanContext)
        .join(models.AuctionScanContext, models.AuctionScanContext.scan_id == models.AuctionScan.id)
        .where(
            models.AuctionScan.complete.is_(True),
            models.AuctionScanContext.region_id.is_not(None),
            models.AuctionScanContext.realm_id.is_not(None),
        )
        .order_by(models.AuctionScan.scanned_at_unix.desc(), models.AuctionScan.id.desc())
    ).all()
    result: list[tuple[models.AuctionScan, models.AuctionScanContext]] = []
    seen: set[tuple[int, int]] = set()
    for scan, context in rows:
        key = (int(context.region_id), int(context.realm_id))
        if key not in seen:
            seen.add(key)
            result.append((scan, context))
    return result


def resolve_complete_scan(db: Session, scan_id: int | None = None) -> models.AuctionScan | None:
    if scan_id is None:
        return latest_complete_scan(db)
    return db.scalar(
        select(models.AuctionScan).where(
            models.AuctionScan.id == scan_id,
            models.AuctionScan.complete.is_(True),
        )
    )


def market_catalog(db: Session) -> dict[str, Any]:
    """Return plugin-labelled realms and every available capture time."""
    rows = db.execute(
        select(models.AuctionScan, models.AuctionScanContext)
        .join(models.AuctionScanContext, models.AuctionScanContext.scan_id == models.AuctionScan.id)
        .where(
            models.AuctionScan.complete.is_(True),
            models.AuctionScanContext.realm_name.is_not(None),
            models.AuctionScanContext.realm_id.is_not(None),
            models.AuctionScanContext.region_id.is_not(None),
        )
        .order_by(models.AuctionScan.scanned_at_unix.desc(), models.AuctionScan.id.desc())
    ).all()
    realms: dict[tuple[int, int], dict[str, Any]] = {}
    for scan, context in rows:
        key = (int(context.region_id), int(context.realm_id))
        realm = realms.setdefault(
            key,
            {
                "key": f"{key[0]}:{key[1]}",
                "region": context.region_name,
                "regionID": key[0],
                "realm": context.realm_name,
                "normalizedRealm": context.normalized_realm_name,
                "realmID": key[1],
                "latestScanId": scan.id,
                "scans": [],
            },
        )
        scanned_at = datetime.fromtimestamp(scan.scanned_at_unix, tz=timezone.utc)
        realm["scans"].append(
            {
                "scanId": scan.id,
                "scannedAt": scanned_at.isoformat().replace("+00:00", "Z"),
                "scannedAtUnix": scan.scanned_at_unix,
                "listingCount": scan.imported_listing_count,
            }
        )
    return {"realms": list(realms.values())}


def _history_scan_ids(db: Session, selected_scan: models.AuctionScan) -> list[int]:
    """History is isolated to the selected plugin-labelled realm and time horizon."""
    context = db.get(models.AuctionScanContext, selected_scan.id)
    if context is None or context.region_id is None or context.realm_id is None:
        return [selected_scan.id]
    return list(
        db.scalars(
            select(models.AuctionScan.id)
            .join(models.AuctionScanContext, models.AuctionScanContext.scan_id == models.AuctionScan.id)
            .where(
                models.AuctionScan.complete.is_(True),
                models.AuctionScan.scanned_at_unix <= selected_scan.scanned_at_unix,
                models.AuctionScanContext.region_id == context.region_id,
                models.AuctionScanContext.realm_id == context.realm_id,
            )
            .order_by(models.AuctionScan.scanned_at_unix.asc(), models.AuctionScan.id.asc())
        ).all()
    )


def _market_scopes_for_items(db: Session, scan_id: int, item_ids: set[int]) -> dict[int, str]:
    if not item_ids:
        return {}
    result: dict[int, str] = {}
    rows = db.execute(
        select(
            models.AuctionItemMarketScope.item_id,
            models.AuctionItemMarketScope.market_scope,
        ).where(
            models.AuctionItemMarketScope.scan_id == scan_id,
            models.AuctionItemMarketScope.item_id.in_(item_ids),
        )
    ).all()
    for item_id, market_scope in rows:
        normalized_scope = str(market_scope)
        numeric_item_id = int(item_id)
        if normalized_scope in {"region", "realm"} or numeric_item_id not in result:
            result[numeric_item_id] = normalized_scope
    return result


def market_status(db: Session, scan_id: int | None = None) -> dict[str, Any]:
    scan = resolve_complete_scan(db, scan_id)
    if scan is None:
        return {"available": False, "complete": False}
    scanned_at = datetime.fromtimestamp(scan.scanned_at_unix, tz=timezone.utc)
    listing = models.AuctionListing
    pet_rows = db.execute(
        select(listing.item_id, listing.battle_pet_creature_id, listing.item_link).where(
            listing.scan_id == scan.id,
            listing.battle_pet_creature_id.is_not(None),
        )
    ).all()
    pet_market_pairs = {(row.item_id, row.battle_pet_creature_id) for row in pet_rows}
    pet_variant_markets = {
        (row.item_id, row.battle_pet_creature_id, _pet_variant_key(_parse_battle_pet_variant(row.item_link)))
        for row in pet_rows
    }
    expanded_market_count = scan.market_item_count - len(pet_market_pairs) + len(pet_variant_markets)
    context = db.get(models.AuctionScanContext, scan.id)
    return {
        "available": True,
        "scanId": scan.id,
        "snapshotSha256": scan.snapshot_sha256,
        "scannedAt": scanned_at.isoformat().replace("+00:00", "Z"),
        "scannedAtUnix": scan.scanned_at_unix,
        "sourceDate": scan.source_date,
        "listingCount": scan.imported_listing_count,
        "uniqueItemCount": scan.unique_item_count,
        "marketItemCount": expanded_market_count,
        "totalQuantity": scan.total_quantity,
        "linkedItemCount": scan.linked_item_count,
        "complete": scan.complete,
        "durationMs": scan.duration_ms,
        "region": (context.region_name if context and context.region_name else os.getenv("WOW_REGION", "CN")),
        "regionID": context.region_id if context else None,
        "realm": (context.realm_name if context and context.realm_name else os.getenv("WOW_REALM") or None),
        "normalizedRealm": context.normalized_realm_name if context else None,
        "realmID": context.realm_id if context else None,
    }


def _metric_change(previous: int | None, current: int | None) -> dict[str, Any] | None:
    if previous is None or current is None:
        return None
    absolute = current - previous
    percent = (absolute / previous * 100) if previous != 0 else None
    return {
        "previous": previous,
        "current": current,
        "absolute": absolute,
        "percent": round(percent, 2) if percent is not None else None,
    }


def item_history(
    db: Session,
    *,
    item_id: int,
    battle_pet_creature_id: int | None,
    item_context: int | None = None,
    pet_variant_key: str | None = None,
    scan_id: int | None = None,
) -> dict[str, Any] | None:
    """Return one aggregate point per complete scan for a market item."""
    if scan_id is None:
        history_scan_ids = list(
            db.scalars(
                select(models.AuctionScan.id)
                .where(models.AuctionScan.complete.is_(True))
                .order_by(models.AuctionScan.scanned_at_unix.asc(), models.AuctionScan.id.asc())
            ).all()
        )
    else:
        selected_scan = resolve_complete_scan(db, scan_id)
        if selected_scan is None:
            return None
        history_scan_ids = _history_scan_ids(db, selected_scan)
    if not history_scan_ids:
        return None
    if pet_variant_key is not None:
        return _item_history_by_pet_variant(
            db,
            item_id=item_id,
            battle_pet_creature_id=battle_pet_creature_id,
            pet_variant_key=pet_variant_key,
            scan_ids=history_scan_ids,
        )
    if item_context is not None:
        return _item_history_by_context(
            db,
            item_id=item_id,
            battle_pet_creature_id=battle_pet_creature_id,
            item_context=item_context,
            scan_ids=history_scan_ids,
        )
    scan = models.AuctionScan
    summary = models.AuctionItemSummary
    pet_key = battle_pet_creature_id or 0
    rows = db.execute(
        select(
            scan.id.label("scan_id"),
            scan.scanned_at_unix,
            scan.source_date,
            summary.name,
            summary.quality_id.label("quality"),
            summary.texture,
            summary.listing_count,
            summary.variant_count,
            summary.total_quantity,
            summary.min_unit_price,
            summary.min_buyout,
            models.AuctionScanContext.realm_name,
            models.AuctionScanContext.realm_id,
            models.AuctionScanContext.region_name,
            models.AuctionScanContext.region_id,
            models.AuctionItemMarketScope.market_scope,
        )
        .join(summary, summary.scan_id == scan.id)
        .join(models.AuctionScanContext, models.AuctionScanContext.scan_id == scan.id)
        .join(
            models.AuctionItemMarketScope,
            (models.AuctionItemMarketScope.scan_id == scan.id)
            & (models.AuctionItemMarketScope.item_id == summary.item_id),
        )
        .where(
            scan.complete.is_(True),
            scan.id.in_(history_scan_ids),
            summary.item_id == item_id,
            summary.battle_pet_creature_id == pet_key,
        )
        .order_by(scan.scanned_at_unix.asc(), scan.id.asc())
    ).mappings().all()
    if not rows:
        return None

    points = []
    for row in rows:
        scanned_at = datetime.fromtimestamp(row["scanned_at_unix"], tz=timezone.utc)
        points.append(
            {
                "scanId": int(row["scan_id"]),
                "scannedAt": scanned_at.isoformat().replace("+00:00", "Z"),
                "scannedAtUnix": int(row["scanned_at_unix"]),
                "sourceDate": row["source_date"],
                "minUnitPrice": (
                    int(row["min_unit_price"]) if row["min_unit_price"] is not None else None
                ),
                "minBuyout": int(row["min_buyout"]) if row["min_buyout"] is not None else None,
                "listingCount": int(row["listing_count"]),
                "variantCount": int(row["variant_count"]),
                "totalQuantity": int(row["total_quantity"] or 0),
                "realm": row["realm_name"],
                "realmID": row["realm_id"],
                "region": row["region_name"],
                "regionID": row["region_id"],
                "marketScope": row["market_scope"],
            }
        )

    first = points[0]
    latest = points[-1]
    return {
        "itemID": item_id,
        "battlePetCreatureID": battle_pet_creature_id,
        "marketKey": f"{item_id}:{battle_pet_creature_id}" if battle_pet_creature_id else str(item_id),
        "name": rows[-1]["name"],
        "quality": int(rows[-1]["quality"]) if rows[-1]["quality"] is not None else None,
        "texture": int(rows[-1]["texture"]) if rows[-1]["texture"] is not None else None,
        "marketScope": rows[-1]["market_scope"],
        "pointCount": len(points),
        "change": {
            "minUnitPrice": _metric_change(first["minUnitPrice"], latest["minUnitPrice"]),
            "minBuyout": _metric_change(first["minBuyout"], latest["minBuyout"]),
            "listingCount": _metric_change(first["listingCount"], latest["listingCount"]),
            "totalQuantity": _metric_change(first["totalQuantity"], latest["totalQuantity"]),
        },
        "points": points,
    }


def _search_filter(q: str | None):
    term = (q or "").strip()
    if not term:
        return None
    summary = models.AuctionItemSummary
    name_match = summary.name.contains(term, autoescape=True)
    if term.isdecimal():
        return or_(summary.item_id == int(term), name_match)
    return name_match


def market_items(
    db: Session,
    *,
    q: str | None,
    page: int,
    page_size: int,
    sort: str,
    collection: str | None = None,
    scan_id: int | None = None,
    _include_crafting_quality: bool = True,
) -> dict[str, Any]:
    if scan_id is None:
        return _unified_market_items(
            db,
            q=q,
            page=page,
            page_size=page_size,
            sort=sort,
            collection=collection,
        )
    scan = resolve_complete_scan(db, scan_id)
    if scan is None:
        return {"scanId": None, "page": page, "pageSize": page_size, "total": 0, "totalPages": 0, "items": []}
    if sort not in SUPPORTED_SORTS:
        raise ValueError(f"不支持的排序: {sort}")
    if collection is not None and collection not in SUPPORTED_COLLECTIONS:
        raise ValueError(f"不支持的快捷分类: {collection}")
    if collection == "raid_boe_12_1":
        return _raid_boe_items_by_tier(
            db,
            scan=scan,
            q=q,
            page=page,
            page_size=page_size,
            sort=sort,
        )

    summary = models.AuctionItemSummary
    search_filter = _search_filter(q)
    filters = [summary.scan_id == scan.id]
    if collection is not None:
        filters.append(summary.item_id.in_(SUPPORTED_COLLECTIONS[collection]))
    if search_filter is not None:
        filters.append(search_filter)

    statement = (
        select(
            summary.item_id,
            summary.battle_pet_creature_id,
            summary.name,
            summary.quality_id.label("quality"),
            summary.texture,
            summary.listing_count,
            summary.variant_count,
            summary.total_quantity,
            summary.min_unit_price,
            summary.min_buyout,
        )
        .where(*filters)
    )
    rows = db.execute(statement).mappings().all()
    scope_by_item_id = _market_scopes_for_items(
        db, scan.id, {int(row["item_id"]) for row in rows}
    )
    items: list[dict[str, Any]] = []
    pet_market_keys = {
        (int(row["item_id"]), int(row["battle_pet_creature_id"]))
        for row in rows
        if row["battle_pet_creature_id"]
    }
    for row in rows:
        item_id = int(row["item_id"])
        if row["battle_pet_creature_id"]:
            continue
        items.append(
            {
                "itemID": item_id,
                "battlePetCreatureID": None,
                "petVariantKey": None,
                "marketKey": str(item_id),
                "marketScope": scope_by_item_id.get(item_id, "unknown"),
                "name": row["name"],
                "quality": int(row["quality"]) if row["quality"] is not None else None,
                "texture": int(row["texture"]) if row["texture"] is not None else None,
                "craftingQuality": None,
                "listingCount": int(row["listing_count"]),
                "variantCount": int(row["variant_count"]),
                "totalQuantity": int(row["total_quantity"] or 0),
                "minUnitPrice": int(row["min_unit_price"]) if row["min_unit_price"] is not None else None,
                "minBuyout": int(row["min_buyout"]) if row["min_buyout"] is not None else None,
                **_pet_variant_defaults(),
            }
        )

    if pet_market_keys:
        listing = models.AuctionListing
        pet_item_ids = {item_id for item_id, _ in pet_market_keys}
        pet_rows = db.scalars(
            select(listing).where(
                listing.scan_id == scan.id,
                listing.item_id.in_(pet_item_ids),
                listing.battle_pet_creature_id.is_not(None),
            )
        ).all()
        groups: dict[tuple[int, int, str], list[tuple[models.AuctionListing, dict[str, Any] | None]]] = {}
        for listing_row in pet_rows:
            market_pair = (listing_row.item_id, int(listing_row.battle_pet_creature_id or 0))
            if market_pair not in pet_market_keys:
                continue
            pet_variant = _parse_battle_pet_variant(listing_row.item_link)
            variant_key = _pet_variant_key(pet_variant)
            groups.setdefault((*market_pair, variant_key), []).append((listing_row, pet_variant))

        for (item_id, pet_creature_id, variant_key), group in groups.items():
            representative, pet_variant = group[0]
            unit_prices = [row.unit_price for row, _ in group if row.unit_price is not None]
            buyouts = [row.buyout_amount for row, _ in group if row.buyout_amount > 0]
            items.append(
                {
                    "itemID": item_id,
                    "battlePetCreatureID": pet_creature_id,
                    "petVariantKey": variant_key,
                    "marketKey": _pet_market_key(item_id, pet_creature_id, variant_key),
                    "marketScope": scope_by_item_id.get(item_id, "unknown"),
                    "name": representative.name,
                    "quality": representative.quality_id,
                    "texture": representative.texture,
                    "craftingQuality": None,
                    "listingCount": len(group),
                    "variantCount": 1,
                    "totalQuantity": sum(row.quantity for row, _ in group),
                    "minUnitPrice": min(unit_prices) if unit_prices else None,
                    "minBuyout": min(buyouts) if buyouts else None,
                    **(pet_variant or _pet_variant_defaults()),
                }
            )

    if sort == "price_asc":
        items.sort(key=lambda item: (item["minUnitPrice"] is None, item["minUnitPrice"] or 0, item["marketKey"]))
    elif sort == "price_desc":
        items.sort(key=lambda item: (item["minUnitPrice"] is None, -(item["minUnitPrice"] or 0), item["marketKey"]))
    elif sort == "quantity_desc":
        items.sort(key=lambda item: (-item["totalQuantity"], item["marketKey"]))
    elif sort == "listings_desc":
        items.sort(key=lambda item: (-item["listingCount"], item["marketKey"]))
    else:
        items.sort(key=lambda item: (item["name"], item["marketKey"]))

    total = len(items)
    start = (page - 1) * page_size
    items = items[start : start + page_size]
    item_ids = {item["itemID"] for item in items if item["battlePetCreatureID"] is None}
    quality_by_market_key: dict[tuple[int, int], int] = {}
    if item_ids and _include_crafting_quality:
        listing = models.AuctionListing
        link_rows = db.execute(
            select(
                listing.item_id,
                listing.battle_pet_creature_id,
                func.min(listing.item_link).label("item_link"),
            )
            .where(listing.scan_id == scan.id, listing.item_id.in_(item_ids))
            .group_by(listing.item_id, listing.battle_pet_creature_id)
        ).mappings().all()
        for link_row in link_rows:
            crafting_quality = _crafting_quality_from_item_link(link_row["item_link"])
            if crafting_quality is not None:
                quality_by_market_key[
                    (int(link_row["item_id"]), int(link_row["battle_pet_creature_id"] or 0))
                ] = crafting_quality

    for item in items:
        if item["battlePetCreatureID"] is None:
            item["craftingQuality"] = quality_by_market_key.get((item["itemID"], 0))
    return {
        "scanId": scan.id,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size),
        "items": items,
    }


def _decorate_history_points(
    db: Session, points: list[dict[str, Any]], item_id: int
) -> str:
    scan_ids = {int(point["scanId"]) for point in points}
    rows = db.execute(
        select(
            models.AuctionScanContext.scan_id,
            models.AuctionScanContext.realm_name,
            models.AuctionScanContext.realm_id,
            models.AuctionScanContext.region_name,
            models.AuctionScanContext.region_id,
            models.AuctionItemMarketScope.market_scope,
        )
        .join(
            models.AuctionItemMarketScope,
            models.AuctionItemMarketScope.scan_id == models.AuctionScanContext.scan_id,
        )
        .where(
            models.AuctionScanContext.scan_id.in_(scan_ids),
            models.AuctionItemMarketScope.item_id == item_id,
        )
    ).all()
    metadata = {
        int(row.scan_id): {
            "realm": row.realm_name,
            "realmID": row.realm_id,
            "region": row.region_name,
            "regionID": row.region_id,
            "marketScope": row.market_scope,
        }
        for row in rows
    }
    for point in points:
        point.update(metadata.get(int(point["scanId"]), {}))
    return str(points[-1].get("marketScope") or "unknown")


def _sort_market_rows(items: list[dict[str, Any]], sort: str) -> None:
    if sort == "price_asc":
        items.sort(key=lambda item: (item["minUnitPrice"] is None, item["minUnitPrice"] or 0, item["marketKey"]))
    elif sort == "price_desc":
        items.sort(key=lambda item: (item["minUnitPrice"] is None, -(item["minUnitPrice"] or 0), item["marketKey"]))
    elif sort == "quantity_desc":
        items.sort(key=lambda item: (-item["totalQuantity"], item["marketKey"]))
    elif sort == "listings_desc":
        items.sort(key=lambda item: (-item["listingCount"], item["marketKey"]))
    else:
        items.sort(key=lambda item: (item["name"], item["marketKey"]))


def _unified_market_items(
    db: Session,
    *,
    q: str | None,
    page: int,
    page_size: int,
    sort: str,
    collection: str | None,
) -> dict[str, Any]:
    """One page across realms: region items dedupe, realm items retain a realm dimension."""
    latest = latest_complete_scans_by_realm(db)
    if not latest:
        return {"scanId": None, "page": page, "pageSize": page_size, "total": 0, "totalPages": 0, "items": []}
    region_rows: dict[str, tuple[int, dict[str, Any]]] = {}
    realm_rows: list[dict[str, Any]] = []
    for scan, context in latest:
        response = market_items(
            db,
            q=q,
            page=1,
            page_size=1_000_000,
            sort=sort,
            collection=collection,
            scan_id=scan.id,
            _include_crafting_quality=False,
        )
        for source in response["items"]:
            item = dict(source)
            item.update(
                {
                    "scanId": scan.id,
                    "scannedAt": datetime.fromtimestamp(
                        scan.scanned_at_unix, tz=timezone.utc
                    ).isoformat().replace("+00:00", "Z"),
                    "scannedAtUnix": scan.scanned_at_unix,
                    "realm": context.realm_name,
                    "realmID": context.realm_id,
                    "region": context.region_name,
                    "regionID": context.region_id,
                }
            )
            if item["marketScope"] == "region":
                current = region_rows.get(item["marketKey"])
                if current is None or scan.scanned_at_unix > current[0]:
                    region_rows[item["marketKey"]] = (scan.scanned_at_unix, item)
            else:
                realm_rows.append(item)
    items = [item for _, item in region_rows.values()] + realm_rows
    _sort_market_rows(items, sort)
    total = len(items)
    start = (page - 1) * page_size
    page_items = items[start : start + page_size]
    _decorate_unified_crafting_quality(db, page_items)
    return {
        "scanId": None,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size),
        "items": page_items,
    }


def _decorate_unified_crafting_quality(db: Session, items: list[dict[str, Any]]) -> None:
    """Read item links only for the final page, not every item in every realm."""
    item_ids_by_scan: dict[int, set[int]] = {}
    for item in items:
        if item["battlePetCreatureID"] is None:
            item_ids_by_scan.setdefault(int(item["scanId"]), set()).add(int(item["itemID"]))
    listing = models.AuctionListing
    for scan_id, item_ids in item_ids_by_scan.items():
        rows = db.execute(
            select(
                listing.item_id,
                func.min(listing.item_link).label("item_link"),
            )
            .where(listing.scan_id == scan_id, listing.item_id.in_(item_ids))
            .group_by(listing.item_id)
        ).mappings().all()
        qualities = {
            int(row["item_id"]): _crafting_quality_from_item_link(row["item_link"])
            for row in rows
        }
        for item in items:
            if int(item["scanId"]) == scan_id and item["battlePetCreatureID"] is None:
                item["craftingQuality"] = qualities.get(int(item["itemID"]))


def _parse_item_variant(item_link: str | None) -> dict[str, Any]:
    """Extract raid difficulty, item level and random secondary stats from an item link."""
    result: dict[str, Any] = {
        "itemContext": None,
        "difficulty": None,
        "itemLevel": None,
        "upgradeTrack": None,
        "upgradeLevel": None,
        "statLabel": None,
        "hasSocket": False,
        "craftingQuality": _crafting_quality_from_item_link(item_link),
    }
    if not item_link:
        return result

    marker = "Hitem:"
    if marker not in item_link:
        return result
    payload = item_link.split(marker, 1)[1].split("|h", 1)[0]
    fields = payload.split(":")
    if len(fields) < 13:
        return result

    try:
        item_context = int(fields[11]) if fields[11] else None
        bonus_count = int(fields[12]) if fields[12] else 0
    except ValueError:
        return result

    bonus_start = 13
    bonus_ids: list[int] = []
    for value in fields[bonus_start : bonus_start + bonus_count]:
        try:
            bonus_ids.append(int(value))
        except ValueError:
            continue

    modifier_count_index = bonus_start + bonus_count
    modifiers: dict[int, int] = {}
    if modifier_count_index < len(fields):
        try:
            modifier_count = int(fields[modifier_count_index] or 0)
        except ValueError:
            modifier_count = 0
        modifier_start = modifier_count_index + 1
        for index in range(modifier_count):
            pair_start = modifier_start + index * 2
            if pair_start + 1 >= len(fields):
                break
            try:
                modifiers[int(fields[pair_start])] = int(fields[pair_start + 1])
            except ValueError:
                continue

    stats = [
        SECONDARY_STAT_LABELS[value]
        for modifier_type in (29, 30)
        if (value := modifiers.get(modifier_type)) in SECONDARY_STAT_LABELS
    ]
    result.update(
        {
            "itemContext": item_context,
            "difficulty": ITEM_CONTEXT_LABELS.get(item_context),
            "itemLevel": next(
                (KNOWN_ITEM_LEVEL_BONUSES[bonus] for bonus in bonus_ids if bonus in KNOWN_ITEM_LEVEL_BONUSES),
                None,
            ),
            "upgradeTrack": "老兵" if 13332 in bonus_ids else None,
            "upgradeLevel": "1/6" if 13332 in bonus_ids else None,
            "statLabel": " > ".join(stats) if stats else None,
            "hasSocket": 13695 in bonus_ids,
        }
    )
    return result


def _raid_boe_items_by_tier(
    db: Session,
    *,
    scan: models.AuctionScan,
    q: str | None,
    page: int,
    page_size: int,
    sort: str,
) -> dict[str, Any]:
    """Aggregate the small raid BoE collection by item and raid difficulty."""
    listing = models.AuctionListing
    term = (q or "").strip()
    rows = db.scalars(
        select(listing).where(
            listing.scan_id == scan.id,
            listing.item_id.in_(RAID_BOE_12_1_ITEM_IDS),
        )
    ).all()

    groups: dict[tuple[int, int | None], list[models.AuctionListing]] = {}
    for row in rows:
        if term and term not in row.name and (not term.isdecimal() or row.item_id != int(term)):
            continue
        context = _parse_item_variant(row.item_link)["itemContext"]
        groups.setdefault((row.item_id, context), []).append(row)

    items: list[dict[str, Any]] = []
    scope_by_item_id = _market_scopes_for_items(db, scan.id, {row.item_id for row in rows})
    for (item_id, context), group in groups.items():
        representative = group[0]
        unit_prices = [row.unit_price for row in group if row.unit_price is not None]
        buyouts = [row.buyout_amount for row in group if row.buyout_amount > 0]
        items.append(
            {
                "itemID": item_id,
                "battlePetCreatureID": None,
                "itemContext": context,
                "difficulty": ITEM_CONTEXT_LABELS.get(context, "未识别难度"),
                "marketKey": f"{item_id}:context:{context if context is not None else 'unknown'}",
                "marketScope": scope_by_item_id.get(item_id, "unknown"),
                "name": representative.name,
                "quality": representative.quality_id,
                "texture": representative.texture,
                "craftingQuality": _crafting_quality_from_item_link(representative.item_link),
                "listingCount": len(group),
                "variantCount": len({row.item_link for row in group}),
                "totalQuantity": sum(row.quantity for row in group),
                "minUnitPrice": min(unit_prices) if unit_prices else None,
                "minBuyout": min(buyouts) if buyouts else None,
            }
        )

    if sort == "price_asc":
        items.sort(key=lambda item: (item["minUnitPrice"] is None, item["minUnitPrice"] or 0, item["itemID"]))
    elif sort == "price_desc":
        items.sort(key=lambda item: (item["minUnitPrice"] is None, -(item["minUnitPrice"] or 0), item["itemID"]))
    elif sort == "quantity_desc":
        items.sort(key=lambda item: (-item["totalQuantity"], item["itemID"]))
    elif sort == "listings_desc":
        items.sort(key=lambda item: (-item["listingCount"], item["itemID"]))
    else:
        items.sort(
            key=lambda item: (
                item["name"],
                ITEM_CONTEXT_ORDER.get(item["itemContext"], 99),
                item["itemID"],
            )
        )

    total = len(items)
    start = (page - 1) * page_size
    return {
        "scanId": scan.id,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size),
        "items": items[start : start + page_size],
    }


def _item_history_by_context(
    db: Session,
    *,
    item_id: int,
    battle_pet_creature_id: int | None,
    item_context: int,
    scan_ids: list[int],
) -> dict[str, Any] | None:
    scan = models.AuctionScan
    listing = models.AuctionListing
    filters = [scan.complete.is_(True), scan.id.in_(scan_ids), listing.item_id == item_id]
    if battle_pet_creature_id is not None:
        filters.append(listing.battle_pet_creature_id == battle_pet_creature_id)
    rows = db.execute(
        select(scan, listing)
        .join(listing, listing.scan_id == scan.id)
        .where(*filters)
        .order_by(scan.scanned_at_unix.asc(), scan.id.asc(), listing.source_index.asc())
    ).all()

    grouped: dict[int, tuple[models.AuctionScan, list[models.AuctionListing]]] = {}
    for scan_row, listing_row in rows:
        if _parse_item_variant(listing_row.item_link)["itemContext"] != item_context:
            continue
        grouped.setdefault(scan_row.id, (scan_row, []))[1].append(listing_row)
    if not grouped:
        return None

    points: list[dict[str, Any]] = []
    latest_listing: models.AuctionListing | None = None
    for scan_row, group in grouped.values():
        latest_listing = group[0]
        unit_prices = [row.unit_price for row in group if row.unit_price is not None]
        buyouts = [row.buyout_amount for row in group if row.buyout_amount > 0]
        scanned_at = datetime.fromtimestamp(scan_row.scanned_at_unix, tz=timezone.utc)
        points.append(
            {
                "scanId": scan_row.id,
                "scannedAt": scanned_at.isoformat().replace("+00:00", "Z"),
                "scannedAtUnix": scan_row.scanned_at_unix,
                "sourceDate": scan_row.source_date,
                "minUnitPrice": min(unit_prices) if unit_prices else None,
                "minBuyout": min(buyouts) if buyouts else None,
                "listingCount": len(group),
                "variantCount": len({row.item_link for row in group}),
                "totalQuantity": sum(row.quantity for row in group),
            }
        )

    assert latest_listing is not None
    market_scope = _decorate_history_points(db, points, item_id)
    first = points[0]
    latest = points[-1]
    return {
        "itemID": item_id,
        "battlePetCreatureID": battle_pet_creature_id,
        "itemContext": item_context,
        "difficulty": ITEM_CONTEXT_LABELS.get(item_context, "未识别难度"),
        "marketKey": f"{item_id}:context:{item_context}",
        "name": latest_listing.name,
        "quality": latest_listing.quality_id,
        "texture": latest_listing.texture,
        "marketScope": market_scope,
        "pointCount": len(points),
        "change": {
            "minUnitPrice": _metric_change(first["minUnitPrice"], latest["minUnitPrice"]),
            "minBuyout": _metric_change(first["minBuyout"], latest["minBuyout"]),
            "listingCount": _metric_change(first["listingCount"], latest["listingCount"]),
            "totalQuantity": _metric_change(first["totalQuantity"], latest["totalQuantity"]),
        },
        "points": points,
    }


def _item_history_by_pet_variant(
    db: Session,
    *,
    item_id: int,
    battle_pet_creature_id: int | None,
    pet_variant_key: str,
    scan_ids: list[int],
) -> dict[str, Any] | None:
    scan = models.AuctionScan
    listing = models.AuctionListing
    filters = [scan.complete.is_(True), scan.id.in_(scan_ids), listing.item_id == item_id]
    if battle_pet_creature_id is not None:
        filters.append(listing.battle_pet_creature_id == battle_pet_creature_id)
    rows = db.execute(
        select(scan, listing)
        .join(listing, listing.scan_id == scan.id)
        .where(*filters)
        .order_by(scan.scanned_at_unix.asc(), scan.id.asc(), listing.source_index.asc())
    ).all()

    grouped: dict[int, tuple[models.AuctionScan, list[models.AuctionListing]]] = {}
    latest_variant: dict[str, Any] | None = None
    for scan_row, listing_row in rows:
        pet_variant = _parse_battle_pet_variant(listing_row.item_link)
        if _pet_variant_key(pet_variant) != pet_variant_key:
            continue
        latest_variant = pet_variant
        grouped.setdefault(scan_row.id, (scan_row, []))[1].append(listing_row)
    if not grouped:
        return None

    points: list[dict[str, Any]] = []
    latest_listing: models.AuctionListing | None = None
    for scan_row, group in grouped.values():
        latest_listing = group[0]
        unit_prices = [row.unit_price for row in group if row.unit_price is not None]
        buyouts = [row.buyout_amount for row in group if row.buyout_amount > 0]
        scanned_at = datetime.fromtimestamp(scan_row.scanned_at_unix, tz=timezone.utc)
        points.append(
            {
                "scanId": scan_row.id,
                "scannedAt": scanned_at.isoformat().replace("+00:00", "Z"),
                "scannedAtUnix": scan_row.scanned_at_unix,
                "sourceDate": scan_row.source_date,
                "minUnitPrice": min(unit_prices) if unit_prices else None,
                "minBuyout": min(buyouts) if buyouts else None,
                "listingCount": len(group),
                "variantCount": 1,
                "totalQuantity": sum(row.quantity for row in group),
            }
        )

    assert latest_listing is not None
    market_scope = _decorate_history_points(db, points, item_id)
    first = points[0]
    latest = points[-1]
    return {
        "itemID": item_id,
        "battlePetCreatureID": battle_pet_creature_id,
        "petVariantKey": pet_variant_key,
        "marketKey": _pet_market_key(item_id, battle_pet_creature_id, pet_variant_key),
        "name": latest_listing.name,
        "quality": latest_listing.quality_id,
        "texture": latest_listing.texture,
        "marketScope": market_scope,
        "pointCount": len(points),
        "change": {
            "minUnitPrice": _metric_change(first["minUnitPrice"], latest["minUnitPrice"]),
            "minBuyout": _metric_change(first["minBuyout"], latest["minBuyout"]),
            "listingCount": _metric_change(first["listingCount"], latest["listingCount"]),
            "totalQuantity": _metric_change(first["totalQuantity"], latest["totalQuantity"]),
        },
        "points": points,
        **(latest_variant or _pet_variant_defaults()),
    }


def item_listings(
    db: Session,
    *,
    item_id: int,
    battle_pet_creature_id: int | None,
    item_context: int | None = None,
    pet_variant_key: str | None = None,
    page: int,
    page_size: int,
    scan_id: int | None = None,
) -> dict[str, Any] | None:
    scan = resolve_complete_scan(db, scan_id)
    if scan is None:
        return None
    listing = models.AuctionListing
    filters = [listing.scan_id == scan.id, listing.item_id == item_id]
    if battle_pet_creature_id is not None:
        filters.append(listing.battle_pet_creature_id == battle_pet_creature_id)
    statement = (
        select(listing)
        .where(*filters)
        .order_by(
            case((listing.unit_price.is_(None), 1), else_=0),
            listing.unit_price.asc(),
            listing.buyout_amount.asc(),
            listing.source_index.asc(),
        )
    )
    parsed_rows = [
        (row, _parse_item_variant(row.item_link), _parse_battle_pet_variant(row.item_link))
        for row in db.scalars(statement).all()
    ]
    if item_context is not None:
        parsed_rows = [
            (row, variant, pet_variant) for row, variant, pet_variant in parsed_rows
            if variant["itemContext"] == item_context
        ]
    if pet_variant_key is not None:
        parsed_rows = [
            (row, variant, pet_variant) for row, variant, pet_variant in parsed_rows
            if _pet_variant_key(pet_variant) == pet_variant_key
        ]
    total = len(parsed_rows)
    if total == 0:
        return None
    representative = parsed_rows[0][0]
    display_columns = {
        "tier": any(
            variant["difficulty"]
            or variant["itemLevel"]
            or variant["upgradeTrack"]
            or variant["upgradeLevel"]
            for _, variant, _ in parsed_rows
        ),
        "attributes": any(
            variant["statLabel"] or variant["hasSocket"]
            for _, variant, _ in parsed_rows
        ),
        "petVariant": any(pet_variant for _, _, pet_variant in parsed_rows),
    }
    start = (page - 1) * page_size
    rows = parsed_rows[start : start + page_size]
    items = []
    for row, variant, pet_variant in rows:
        items.append(
            {
                "itemID": row.item_id,
                "name": row.name,
                "quality": row.quality_id,
                "texture": row.texture,
                "battlePetCreatureID": row.battle_pet_creature_id,
                "quantity": row.quantity,
                "unitPrice": row.unit_price,
                "buyoutAmount": row.buyout_amount,
                "minBid": row.min_bid,
                "bidAmount": row.bid_amount,
                "itemLink": row.item_link,
                "timeLeftBand": row.time_left_band,
                "timeLeftLabel": label_time_left_band(row.time_left_band),
                **variant,
                **(pet_variant or _pet_variant_defaults()),
            }
        )
    return {
        "scanId": scan.id,
        "itemID": item_id,
        "battlePetCreatureID": battle_pet_creature_id,
        "itemContext": item_context,
        "petVariantKey": pet_variant_key,
        "difficulty": ITEM_CONTEXT_LABELS.get(item_context) if item_context is not None else None,
        "marketKey": (
            _pet_market_key(item_id, battle_pet_creature_id, pet_variant_key)
            if pet_variant_key is not None
            else f"{item_id}:context:{item_context}"
            if item_context is not None
            else f"{item_id}:{battle_pet_creature_id}" if battle_pet_creature_id else str(item_id)
        ),
        "name": representative.name,
        "quality": representative.quality_id,
        "texture": representative.texture,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size),
        "displayColumns": display_columns,
        "items": items,
    }
