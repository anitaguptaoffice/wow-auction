"""Unauthenticated, read-only market snapshot API."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.market_queries import item_history, item_listings, market_items, market_status

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/status")
def read_market_status(db: Session = Depends(get_db)):
    return market_status(db)


@router.get("/items")
def read_market_items(
    q: str | None = Query(default=None, max_length=100),
    collection: Literal["raid_boe_12_1"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    sort: Literal["price_asc", "price_desc", "quantity_desc", "listings_desc", "name_asc"] = "price_asc",
    db: Session = Depends(get_db),
):
    return market_items(db, q=q, page=page, page_size=page_size, sort=sort, collection=collection)


@router.get("/items/{item_id}/listings")
def read_item_listings(
    item_id: int,
    battle_pet_creature_id: int | None = Query(default=None, ge=1),
    item_context: int | None = Query(default=None, ge=0),
    pet_variant_key: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    result = item_listings(
        db,
        item_id=item_id,
        battle_pet_creature_id=battle_pet_creature_id,
        item_context=item_context,
        pet_variant_key=pet_variant_key,
        page=page,
        page_size=page_size,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="当前快照中没有该物品")
    return result


@router.get("/items/{item_id}/history")
def read_item_history(
    item_id: int,
    battle_pet_creature_id: int | None = Query(default=None, ge=1),
    item_context: int | None = Query(default=None, ge=0),
    pet_variant_key: str | None = Query(default=None, max_length=100),
    db: Session = Depends(get_db),
):
    result = item_history(
        db,
        item_id=item_id,
        battle_pet_creature_id=battle_pet_creature_id,
        item_context=item_context,
        pet_variant_key=pet_variant_key,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="历史快照中没有该物品")
    return result
