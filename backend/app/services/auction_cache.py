import os
import re
import threading
import time
from types import MappingProxyType

from slpp import slpp

from app.config import AUCTION_LUA_PATH

LUA_FILE_PATH = str(AUCTION_LUA_PATH)

_auction_data_cache = tuple()
_cache_lock = threading.Lock()
_last_mtime = 0


def _load_and_process_lua_file():
    try:
        with open(LUA_FILE_PATH, encoding="utf-8") as f:
            lua_content = f.read()
    except FileNotFoundError:
        print(f"Warning: {LUA_FILE_PATH} not found.")
        return []

    match = re.search(r"=\s*(\{.*\})", lua_content, re.DOTALL)
    if not match:
        return []

    lua_table_str = match.group(1)
    try:
        data = slpp.decode(lua_table_str)
    except Exception as e:
        print(f"Error decoding Lua data: {e}")
        return []

    if not isinstance(data, dict) or "auctions" not in data:
        return []

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
