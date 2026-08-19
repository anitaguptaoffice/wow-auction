"""Indexed SQL queries for the public market API."""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app import models
from app.services.auction_labels import label_time_left_band

SUPPORTED_SORTS = {"price_asc", "price_desc", "quantity_desc", "listings_desc", "name_asc"}


def latest_complete_scan(db: Session) -> models.AuctionScan | None:
    return db.scalar(
        select(models.AuctionScan)
        .where(models.AuctionScan.complete.is_(True))
        .order_by(models.AuctionScan.scanned_at_unix.desc(), models.AuctionScan.id.desc())
        .limit(1)
    )


def market_status(db: Session) -> dict[str, Any]:
    scan = latest_complete_scan(db)
    if scan is None:
        return {"available": False, "complete": False}
    scanned_at = datetime.fromtimestamp(scan.scanned_at_unix, tz=timezone.utc)
    return {
        "available": True,
        "scanId": scan.id,
        "snapshotSha256": scan.snapshot_sha256,
        "scannedAt": scanned_at.isoformat().replace("+00:00", "Z"),
        "scannedAtUnix": scan.scanned_at_unix,
        "sourceDate": scan.source_date,
        "listingCount": scan.imported_listing_count,
        "uniqueItemCount": scan.unique_item_count,
        "marketItemCount": scan.market_item_count,
        "totalQuantity": scan.total_quantity,
        "linkedItemCount": scan.linked_item_count,
        "complete": scan.complete,
        "durationMs": scan.duration_ms,
        "region": os.getenv("WOW_REGION", "CN"),
        "realm": os.getenv("WOW_REALM") or None,
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
) -> dict[str, Any]:
    scan = latest_complete_scan(db)
    if scan is None:
        return {"scanId": None, "page": page, "pageSize": page_size, "total": 0, "totalPages": 0, "items": []}
    if sort not in SUPPORTED_SORTS:
        raise ValueError(f"不支持的排序: {sort}")

    summary = models.AuctionItemSummary
    search_filter = _search_filter(q)
    filters = [summary.scan_id == scan.id]
    if search_filter is not None:
        filters.append(search_filter)

    total = int(db.scalar(select(func.count(summary.id)).where(*filters)) or 0)

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

    if sort == "price_asc":
        statement = statement.order_by(
            case((summary.min_unit_price.is_(None), 1), else_=0),
            summary.min_unit_price.asc(),
            summary.item_id.asc(),
            summary.battle_pet_creature_id.asc(),
        )
    elif sort == "price_desc":
        statement = statement.order_by(
            case((summary.min_unit_price.is_(None), 1), else_=0),
            summary.min_unit_price.desc(),
            summary.item_id.asc(),
            summary.battle_pet_creature_id.asc(),
        )
    elif sort == "quantity_desc":
        statement = statement.order_by(
            summary.total_quantity.desc(), summary.item_id.asc(), summary.battle_pet_creature_id.asc()
        )
    elif sort == "listings_desc":
        statement = statement.order_by(
            summary.listing_count.desc(), summary.item_id.asc(), summary.battle_pet_creature_id.asc()
        )
    else:
        statement = statement.order_by(
            summary.name.asc(), summary.item_id.asc(), summary.battle_pet_creature_id.asc()
        )

    rows = db.execute(statement.offset((page - 1) * page_size).limit(page_size)).mappings().all()
    items = [
        {
            "itemID": int(row["item_id"]),
            "battlePetCreatureID": (
                int(row["battle_pet_creature_id"]) if row["battle_pet_creature_id"] else None
            ),
            "marketKey": (
                f'{int(row["item_id"])}:{int(row["battle_pet_creature_id"])}'
                if row["battle_pet_creature_id"]
                else str(int(row["item_id"]))
            ),
            "name": row["name"],
            "quality": int(row["quality"]) if row["quality"] is not None else None,
            "texture": int(row["texture"]) if row["texture"] is not None else None,
            "listingCount": int(row["listing_count"]),
            "variantCount": int(row["variant_count"]),
            "totalQuantity": int(row["total_quantity"] or 0),
            "minUnitPrice": int(row["min_unit_price"]) if row["min_unit_price"] is not None else None,
            "minBuyout": int(row["min_buyout"]) if row["min_buyout"] is not None else None,
        }
        for row in rows
    ]
    return {
        "scanId": scan.id,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size),
        "items": items,
    }


def item_listings(
    db: Session,
    *,
    item_id: int,
    battle_pet_creature_id: int | None,
    page: int,
    page_size: int,
) -> dict[str, Any] | None:
    scan = latest_complete_scan(db)
    if scan is None:
        return None
    listing = models.AuctionListing
    filters = [listing.scan_id == scan.id, listing.item_id == item_id]
    if battle_pet_creature_id is not None:
        filters.append(listing.battle_pet_creature_id == battle_pet_creature_id)
    total = int(db.scalar(select(func.count(listing.id)).where(*filters)) or 0)
    if total == 0:
        return None
    representative = db.scalar(select(listing).where(*filters).order_by(listing.source_index).limit(1))
    if representative is None:
        return None

    statement = (
        select(listing)
        .where(*filters)
        .order_by(
            case((listing.unit_price.is_(None), 1), else_=0),
            listing.unit_price.asc(),
            listing.buyout_amount.asc(),
            listing.source_index.asc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = db.scalars(statement).all()
    items = []
    for row in rows:
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
            }
        )
    return {
        "scanId": scan.id,
        "itemID": item_id,
        "battlePetCreatureID": battle_pet_creature_id,
        "marketKey": f"{item_id}:{battle_pet_creature_id}" if battle_pet_creature_id else str(item_id),
        "name": representative.name,
        "quality": representative.quality_id,
        "texture": representative.texture,
        "page": page,
        "pageSize": page_size,
        "total": total,
        "totalPages": math.ceil(total / page_size),
        "items": items,
    }
