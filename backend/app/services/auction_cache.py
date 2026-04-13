import os
import re
import threading
import time
from types import MappingProxyType
from typing import Any

from slpp import slpp

from app.config import AUCTION_LUA_PATH
from app.services.auction_labels import label_time_left_band

LUA_FILE_PATH = str(AUCTION_LUA_PATH)

_auction_data_cache = tuple()
_cache_lock = threading.Lock()
_last_mtime = 0.0
# 与文件 mtime 绑定的完整解析结果（含多天 scans），供历史查询
_parsed_db: dict[str, Any] | None = None


def _read_lua_content() -> str | None:
    try:
        with open(LUA_FILE_PATH, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        print(f"Warning: {LUA_FILE_PATH} not found.")
        return None


def _decode_auction_db(lua_content: str) -> dict[str, Any] | None:
    match = re.search(r"=\s*(\{.*\})", lua_content, re.DOTALL)
    if not match:
        return None
    lua_table_str = match.group(1)
    try:
        data = slpp.decode(lua_table_str)
    except Exception as e:
        print(f"Error decoding Lua data: {e}")
        return None
    if not isinstance(data, dict) or "auctions" not in data:
        return None
    return data


def _latest_items_from_db(data: dict[str, Any]) -> list[dict[str, Any]]:
    auctions = data.get("auctions", {})
    if not auctions:
        return []
    latest_date = max(auctions.keys())
    scans = auctions.get(latest_date, {}).get("scans", [])
    if not scans:
        return []
    latest_scan = max(scans, key=lambda s: s.get("timestamp", 0))
    items = latest_scan.get("items", [])
    return [item for item in items if item.get("buyoutAmount") and item["buyoutAmount"] > 0]


def _load_and_process_lua_file():
    global _parsed_db
    lua_content = _read_lua_content()
    if not lua_content:
        _parsed_db = None
        return []
    data = _decode_auction_db(lua_content)
    if not data:
        _parsed_db = None
        return []
    _parsed_db = data
    return _latest_items_from_db(data)


def _update_cache():
    global _auction_data_cache
    print("Attempting to update auction data cache...")
    items = _load_and_process_lua_file()
    snapshot = tuple(MappingProxyType(item) for item in items)
    with _cache_lock:
        _auction_data_cache = snapshot
    print(f"Cache updated successfully. Found {len(items)} items.")


def _monitor_file_changes():
    global _last_mtime
    while True:
        try:
            mtime = os.path.getmtime(LUA_FILE_PATH)
            if mtime != _last_mtime:
                print(f"File change detected in {LUA_FILE_PATH}.")
                _last_mtime = mtime
                _update_cache()
        except FileNotFoundError:
            pass
        time.sleep(10)


def start_monitoring():
    print("Starting auction data monitor...")
    try:
        global _last_mtime
        _last_mtime = os.path.getmtime(LUA_FILE_PATH)
        _update_cache()
    except FileNotFoundError:
        print(f"Warning: {LUA_FILE_PATH} not found on initial load. Cache will be empty.")

    monitor_thread = threading.Thread(target=_monitor_file_changes, daemon=True)
    monitor_thread.start()
    print("Auction data monitor started in background.")


def get_cached_auction_items():
    return _auction_data_cache


def _ensure_parsed_db() -> dict[str, Any] | None:
    """若监控线程尚未写入（例如冷启动竞态），按需解析一次。"""
    global _parsed_db
    with _cache_lock:
        if _parsed_db is not None:
            return _parsed_db
    lua_content = _read_lua_content()
    if not lua_content:
        return None
    data = _decode_auction_db(lua_content)
    if data:
        with _cache_lock:
            _parsed_db = data
    return data


def get_auction_history(
    item_id: int,
    days: int = 7,
    item_link: str | None = None,
) -> list[dict[str, Any]]:
    """
    跨所有日期与 scans，返回带时间戳的拍卖记录（用于价格趋势）。
    item_link 若提供则只保留与该链接完全一致的条目（同一物品不同词缀）。
    """
    data = _ensure_parsed_db()
    if not data:
        return []
    cutoff = time.time() - max(1, days) * 86400
    auctions = data.get("auctions") or {}
    points: list[dict[str, Any]] = []
    for _date_str, day in auctions.items():
        if not isinstance(day, dict):
            continue
        for scan in day.get("scans") or []:
            if not isinstance(scan, dict):
                continue
            ts = int(scan.get("timestamp") or 0)
            if ts < cutoff:
                continue
            for item in scan.get("items") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("itemID") != item_id:
                    continue
                if item_link is not None and item.get("itemLink") != item_link:
                    continue
                bo = item.get("buyoutAmount")
                if not bo or bo <= 0:
                    continue
                raw = {
                    "timestamp": ts,
                    "date": _date_str,
                    "buyoutAmount": bo,
                    "minBid": item.get("minBid"),
                    "bidAmount": item.get("bidAmount"),
                    "quantity": item.get("quantity"),
                    "name": item.get("name"),
                    "itemLink": item.get("itemLink"),
                    "timeLeftBand": item.get("timeLeftBand"),
                }
                raw["timeLeftLabel"] = label_time_left_band(raw.get("timeLeftBand"))
                points.append(raw)
    points.sort(key=lambda p: p["timestamp"])
    return points
