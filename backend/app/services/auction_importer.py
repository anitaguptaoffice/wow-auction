"""Transactional import of WoW AuctionSearch SavedVariables snapshots."""

from __future__ import annotations

import hashlib
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from slpp import slpp
from sqlalchemy import Engine, func, insert, select, text
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, engine as default_engine

_LUA_ASSIGNMENT = re.compile(r"=\s*(\{.*\})", re.DOTALL)
_CORE_FIELDS = ("itemID", "name", "quantity", "minBid", "buyoutAmount", "bidAmount", "timeLeftBand")


class SnapshotValidationError(ValueError):
    """The snapshot cannot be proven complete and therefore was not imported."""


class ImportBusyError(RuntimeError):
    """Another process currently owns the database-level import lock."""


@dataclass(frozen=True)
class ValidatedScan:
    source_date: str
    source_scan_index: int
    timestamp: int
    declared_item_count: int
    record_count: int
    unique_item_count: int
    market_item_count: int
    total_quantity: int
    linked_item_count: int
    missing_core_count: int
    incomplete_info_count: int
    api_error_count: int
    duration_ms: float | None
    fingerprint: str
    items: list[dict[str, Any]]
    summaries: list[dict[str, Any]]


@dataclass(frozen=True)
class ImportResult:
    snapshot_sha256: str
    source_size: int
    source_scan_count: int
    imported_scan_count: int
    skipped_scan_count: int
    imported_listing_count: int
    existing_listing_count: int
    duplicate_snapshot: bool
    scan_ids: tuple[int, ...]

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["scan_ids"] = list(self.scan_ids)
        return result


def sha256_file(path: Path, block_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(block_size):
            digest.update(block)
    return digest.hexdigest()


def decode_snapshot(path: Path) -> dict[str, Any]:
    try:
        lua_content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SnapshotValidationError(f"快照不是有效的 UTF-8 文件: {path}") from exc

    match = _LUA_ASSIGNMENT.search(lua_content)
    if not match:
        raise SnapshotValidationError("找不到 SavedVariables 顶层 Lua 表")
    try:
        decoded = slpp.decode(match.group(1))
    except Exception as exc:
        raise SnapshotValidationError(f"Lua 快照解析失败: {exc}") from exc
    if not isinstance(decoded, dict) or not isinstance(decoded.get("auctions"), dict):
        raise SnapshotValidationError("快照缺少 auctions 表")
    return decoded


def _ordered_values(value: Any, label: str) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        try:
            return [item for _, item in sorted(value.items(), key=lambda pair: int(pair[0]))]
        except (TypeError, ValueError) as exc:
            raise SnapshotValidationError(f"{label} 不是连续数组") from exc
    if value is None:
        return []
    raise SnapshotValidationError(f"{label} 必须是数组")


def _required_int(scan: dict[str, Any], key: str, label: str) -> int:
    value = scan.get(key)
    if isinstance(value, bool):
        raise SnapshotValidationError(f"{label}.{key} 不是整数")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise SnapshotValidationError(f"{label}.{key} 不是整数") from exc


def _update_fingerprint(digest, key: str, value: Any) -> None:
    if value is None:
        encoded = b"n"
    elif isinstance(value, bool):
        encoded = b"b1" if value else b"b0"
    elif isinstance(value, int):
        encoded = b"i" + str(value).encode("ascii")
    elif isinstance(value, float):
        encoded = b"f" + value.hex().encode("ascii")
    else:
        encoded = b"s" + str(value).encode("utf-8")
    key_bytes = key.encode("ascii")
    digest.update(len(key_bytes).to_bytes(2, "big"))
    digest.update(key_bytes)
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)


def _optional_int(value: Any) -> int | None:
    return int(value) if value is not None else None


def validate_snapshot(decoded: dict[str, Any]) -> list[ValidatedScan]:
    validated: list[ValidatedScan] = []
    auctions = decoded["auctions"]
    for source_date in sorted(auctions):
        day = auctions[source_date]
        if not isinstance(day, dict):
            raise SnapshotValidationError(f"auctions[{source_date!r}] 不是表")
        scans = _ordered_values(day.get("scans"), f"auctions[{source_date!r}].scans")
        for scan_index, raw_scan in enumerate(scans):
            label = f"{source_date} scan[{scan_index}]"
            if not isinstance(raw_scan, dict):
                raise SnapshotValidationError(f"{label} 不是表")
            items = _ordered_values(raw_scan.get("items"), f"{label}.items")
            if any(not isinstance(item, dict) for item in items):
                raise SnapshotValidationError(f"{label}.items 包含非表记录")

            timestamp = _required_int(raw_scan, "timestamp", label)
            declared = _required_int(raw_scan, "itemCount", label)
            records = _required_int(raw_scan, "recordCount", label)
            linked = _required_int(raw_scan, "linkedItemCount", label)
            missing_core = _required_int(raw_scan, "missingCoreCount", label)
            incomplete = _required_int(raw_scan, "incompleteInfoCount", label)
            api_errors = _required_int(raw_scan, "apiErrorCount", label)

            if declared != records or records != len(items):
                raise SnapshotValidationError(
                    f"{label} 条数不一致: itemCount={declared}, recordCount={records}, items={len(items)}"
                )
            if missing_core or incomplete or api_errors:
                raise SnapshotValidationError(
                    f"{label} 未通过插件完整性检查: missingCore={missing_core}, "
                    f"incompleteInfo={incomplete}, apiErrors={api_errors}"
                )
            if linked != records:
                raise SnapshotValidationError(
                    f"{label} 物品链接不完整: linkedItemCount={linked}, recordCount={records}"
                )

            actual_missing = 0
            unique_item_ids: set[int] = set()
            summaries_by_market: dict[tuple[int, int], dict[str, Any]] = {}
            fingerprint = hashlib.sha256()
            for key, value in (
                ("source_date", str(source_date)),
                ("timestamp", timestamp),
                ("declared_item_count", declared),
                ("record_count", records),
            ):
                _update_fingerprint(fingerprint, key, value)
            total_quantity = 0
            for source_index, item in enumerate(items):
                if (
                    any(item.get(field) is None for field in _CORE_FIELDS)
                    or item.get("hasAllInfo") is not True
                    or not item.get("itemLink")
                ):
                    actual_missing += 1
                    continue
                try:
                    item_id = int(item["itemID"])
                    quantity = int(item["quantity"])
                    min_bid = int(item["minBid"])
                    buyout = int(item["buyoutAmount"])
                    bid = int(item["bidAmount"])
                    time_left = int(item["timeLeftBand"])
                except (TypeError, ValueError):
                    actual_missing += 1
                    continue
                if item_id <= 0 or quantity <= 0 or min_bid < 0 or buyout < 0 or bid < 0 or not (1 <= time_left <= 4):
                    actual_missing += 1
                    continue
                mapping = _listing_mapping(0, source_index, item)
                for field in sorted(mapping):
                    if field != "scan_id":
                        _update_fingerprint(fingerprint, field, mapping[field])

                total_quantity += quantity
                unique_item_ids.add(item_id)
                pet_creature_id = int(item.get("battlePetCreatureID") or 0)
                market_key = (item_id, pet_creature_id)
                summary = summaries_by_market.get(market_key)
                if summary is None:
                    summary = {
                        "item_id": item_id,
                        "battle_pet_creature_id": pet_creature_id,
                        "name": str(item["name"]),
                        "quality_id": _optional_int(item.get("qualityID")),
                        "texture": _optional_int(item.get("texture")),
                        "listing_count": 0,
                        "total_quantity": 0,
                        "min_unit_price": None,
                        "min_buyout": None,
                        "_item_links": set(),
                    }
                    summaries_by_market[market_key] = summary
                summary["listing_count"] += 1
                summary["total_quantity"] += quantity
                if item.get("itemLink") is not None:
                    summary["_item_links"].add(str(item["itemLink"]))
                if item.get("qualityID") is not None:
                    current_quality = summary["quality_id"]
                    summary["quality_id"] = max(int(current_quality or 0), int(item["qualityID"]))
                if item.get("texture") is not None:
                    summary["texture"] = int(item["texture"])
                if buyout > 0:
                    unit_price = (buyout + quantity - 1) // quantity
                    if summary["min_unit_price"] is None or unit_price < summary["min_unit_price"]:
                        summary["min_unit_price"] = unit_price
                    if summary["min_buyout"] is None or buyout < summary["min_buyout"]:
                        summary["min_buyout"] = buyout
            if actual_missing:
                raise SnapshotValidationError(f"{label} 实际发现 {actual_missing} 条核心字段无效记录")

            summaries = []
            for summary in summaries_by_market.values():
                summary["variant_count"] = len(summary.pop("_item_links"))
                summaries.append(summary)

            duration = raw_scan.get("durationMs")
            validated.append(
                ValidatedScan(
                    source_date=str(source_date),
                    source_scan_index=scan_index,
                    timestamp=timestamp,
                    declared_item_count=declared,
                    record_count=records,
                    unique_item_count=len(unique_item_ids),
                    market_item_count=len(summaries),
                    total_quantity=total_quantity,
                    linked_item_count=linked,
                    missing_core_count=missing_core,
                    incomplete_info_count=incomplete,
                    api_error_count=api_errors,
                    duration_ms=float(duration) if duration is not None else None,
                    fingerprint=fingerprint.hexdigest(),
                    items=items,
                    summaries=summaries,
                )
            )
    if not validated:
        raise SnapshotValidationError("快照中没有可导入的 scan")
    return validated


def _listing_mapping(scan_id: int, source_index: int, item: dict[str, Any]) -> dict[str, Any]:
    quantity = int(item["quantity"])
    buyout = int(item["buyoutAmount"])
    return {
        "scan_id": scan_id,
        "source_index": source_index,
        "item_id": int(item["itemID"]),
        "name": str(item["name"]),
        "texture": _optional_int(item.get("texture")),
        "quantity": quantity,
        "quality_id": _optional_int(item.get("qualityID")),
        "usable": bool(item["usable"]) if item.get("usable") is not None else None,
        "level": _optional_int(item.get("level")),
        "level_type": item.get("levelType"),
        "min_bid": int(item["minBid"]),
        "min_increment": _optional_int(item.get("minIncrement")),
        "buyout_amount": buyout,
        "unit_price": (buyout + quantity - 1) // quantity if buyout > 0 else None,
        "bid_amount": int(item["bidAmount"]),
        "high_bidder": bool(item["highBidder"]) if item.get("highBidder") is not None else None,
        "bidder_full_name": item.get("bidderFullName"),
        "owner": item.get("owner"),
        "owner_full_name": item.get("ownerFullName"),
        "sale_status": _optional_int(item.get("saleStatus")),
        "has_all_info": bool(item["hasAllInfo"]) if item.get("hasAllInfo") is not None else None,
        "item_link": item.get("itemLink"),
        "time_left_band": int(item["timeLeftBand"]),
        "battle_pet_creature_id": _optional_int(item.get("battlePetCreatureID")),
        "battle_pet_display_id": _optional_int(item.get("battlePetDisplayID")),
    }


def _chunks(values: Iterable[dict[str, Any]], chunk_size: int) -> Iterator[list[dict[str, Any]]]:
    chunk: list[dict[str, Any]] = []
    for value in values:
        chunk.append(value)
        if len(chunk) >= chunk_size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


@contextmanager
def _database_import_lock(db_engine: Engine):
    if db_engine.dialect.name not in {"mysql", "mariadb"}:
        yield
        return
    connection = db_engine.connect()
    acquired = False
    try:
        acquired = connection.scalar(text("SELECT GET_LOCK('wow_auction_import', 0)")) == 1
        if not acquired:
            raise ImportBusyError("另一实例正在导入拍卖快照")
        yield
    finally:
        if acquired:
            try:
                connection.execute(text("SELECT RELEASE_LOCK('wow_auction_import')"))
            except Exception:
                pass
        connection.close()


def _import_snapshot(path: Path, *, selected_engine: Engine, chunk_size: int) -> ImportResult:
    if chunk_size < 100:
        raise ValueError("chunk_size 必须至少为 100")
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    Base.metadata.create_all(selected_engine)

    snapshot_sha = sha256_file(path)
    source_size = path.stat().st_size
    factory = sessionmaker(bind=selected_engine, autoflush=False, expire_on_commit=False)
    with factory() as session:
        duplicate = session.scalar(
            select(models.AuctionSnapshot).where(models.AuctionSnapshot.sha256 == snapshot_sha)
        )
        if duplicate is not None:
            scan_ids = tuple(
                session.scalars(
                    select(models.AuctionScan.id)
                    .where(models.AuctionScan.snapshot_id == duplicate.id)
                    .order_by(models.AuctionScan.id)
                )
            )
            existing_listings = session.scalar(
                select(func.coalesce(func.sum(models.AuctionScan.imported_listing_count), 0)).where(
                    models.AuctionScan.snapshot_id == duplicate.id
                )
            )
            return ImportResult(
                snapshot_sha256=snapshot_sha,
                source_size=source_size,
                source_scan_count=duplicate.source_scan_count,
                imported_scan_count=0,
                skipped_scan_count=duplicate.source_scan_count,
                imported_listing_count=0,
                existing_listing_count=int(existing_listings or 0),
                duplicate_snapshot=True,
                scan_ids=scan_ids,
            )

    decoded = decode_snapshot(path)
    scans = validate_snapshot(decoded)
    imported_listing_count = 0
    existing_listing_count = 0
    imported_scan_count = 0
    skipped_scan_count = 0
    scan_ids: list[int] = []

    with factory.begin() as session:
        snapshot = models.AuctionSnapshot(
            sha256=snapshot_sha,
            source_path=str(path),
            source_size=source_size,
            source_scan_count=len(scans),
            imported_scan_count=0,
        )
        session.add(snapshot)
        session.flush()

        for scan in scans:
            existing_id = session.scalar(
                select(models.AuctionScan.id).where(models.AuctionScan.scan_fingerprint == scan.fingerprint)
            )
            if existing_id is not None:
                skipped_scan_count += 1
                scan_ids.append(existing_id)
                existing_listing_count += int(
                    session.scalar(
                        select(models.AuctionScan.imported_listing_count).where(models.AuctionScan.id == existing_id)
                    )
                    or 0
                )
                continue

            scan_row = models.AuctionScan(
                snapshot_id=snapshot.id,
                snapshot_sha256=snapshot_sha,
                scan_fingerprint=scan.fingerprint,
                source_date=scan.source_date,
                source_scan_index=scan.source_scan_index,
                scanned_at=datetime.fromtimestamp(scan.timestamp, tz=timezone.utc),
                scanned_at_unix=scan.timestamp,
                declared_item_count=scan.declared_item_count,
                record_count=scan.record_count,
                imported_listing_count=0,
                unique_item_count=scan.unique_item_count,
                market_item_count=scan.market_item_count,
                total_quantity=scan.total_quantity,
                linked_item_count=scan.linked_item_count,
                missing_core_count=scan.missing_core_count,
                incomplete_info_count=scan.incomplete_info_count,
                api_error_count=scan.api_error_count,
                duration_ms=scan.duration_ms,
                complete=False,
            )
            session.add(scan_row)
            session.flush()

            mappings = (
                _listing_mapping(scan_row.id, source_index, item)
                for source_index, item in enumerate(scan.items)
            )
            inserted = 0
            for chunk in _chunks(mappings, chunk_size):
                session.execute(insert(models.AuctionListing), chunk)
                inserted += len(chunk)

            persisted = session.scalar(
                select(func.count(models.AuctionListing.id)).where(models.AuctionListing.scan_id == scan_row.id)
            )
            if inserted != scan.record_count or persisted != scan.record_count:
                raise RuntimeError(
                    f"scan {scan.timestamp} 入库条数不一致: expected={scan.record_count}, "
                    f"inserted={inserted}, persisted={persisted}"
                )

            summary_mappings = ({"scan_id": scan_row.id, **summary} for summary in scan.summaries)
            summary_inserted = 0
            for chunk in _chunks(summary_mappings, chunk_size):
                session.execute(insert(models.AuctionItemSummary), chunk)
                summary_inserted += len(chunk)
            summary_persisted = session.scalar(
                select(func.count(models.AuctionItemSummary.id)).where(
                    models.AuctionItemSummary.scan_id == scan_row.id
                )
            )
            if summary_inserted != scan.market_item_count or summary_persisted != scan.market_item_count:
                raise RuntimeError(
                    f"scan {scan.timestamp} 聚合条数不一致: expected={scan.market_item_count}, "
                    f"inserted={summary_inserted}, persisted={summary_persisted}"
                )
            scan_row.imported_listing_count = inserted
            scan_row.complete = True
            imported_listing_count += inserted
            imported_scan_count += 1
            scan_ids.append(scan_row.id)

        snapshot.imported_scan_count = imported_scan_count

    return ImportResult(
        snapshot_sha256=snapshot_sha,
        source_size=source_size,
        source_scan_count=len(scans),
        imported_scan_count=imported_scan_count,
        skipped_scan_count=skipped_scan_count,
        imported_listing_count=imported_listing_count,
        existing_listing_count=existing_listing_count,
        duplicate_snapshot=False,
        scan_ids=tuple(scan_ids),
    )


def import_snapshot(path: Path, *, db_engine: Engine | None = None, chunk_size: int = 5000) -> ImportResult:
    """Validate and atomically import a snapshot; identical files are a no-op."""
    selected_engine = db_engine or default_engine
    with _database_import_lock(selected_engine):
        return _import_snapshot(path, selected_engine=selected_engine, chunk_size=chunk_size)
