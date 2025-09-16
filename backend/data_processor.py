import re
import os
import time
import threading
from slpp import slpp

LUA_FILE_PATH = "data/auction.lua"

# --- Cache and Threading Globals ---
_auction_data_cache = []
_cache_lock = threading.Lock()
_last_mtime = 0

def _load_and_process_lua_file():
    """
    Reads the Lua auction data file, parses it, and returns the most recent list of items.
    This is the core data processing logic.
    """
    try:
        with open(LUA_FILE_PATH, "r", encoding="utf-8") as f:
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

    filtered_items = [
        item for item in items
        if item.get("buyoutAmount") and item["buyoutAmount"] > 0
    ]
    return filtered_items

def _update_cache():
    """Loads data from file and updates the in-memory cache."""
    global _auction_data_cache
    print("Attempting to update auction data cache...")
    items = _load_and_process_lua_file()
    with _cache_lock:
        _auction_data_cache = items
    print(f"Cache updated successfully. Found {len(items)} items.")

def _monitor_file_changes():
    """Runs in a background thread to check for file modifications and update cache."""
    global _last_mtime
    while True:
        try:
            mtime = os.path.getmtime(LUA_FILE_PATH)
            if mtime != _last_mtime:
                print(f"File change detected in {LUA_FILE_PATH}.")
                _last_mtime = mtime
                _update_cache()
        except FileNotFoundError:
            # If file is not found, wait and try again
            pass
        time.sleep(10) # Check every 10 seconds

def start_monitoring():
    """Initializes the cache and starts the file monitoring background thread."""
    print("Starting auction data monitor...")
    # Initial load
    try:
        global _last_mtime
        _last_mtime = os.path.getmtime(LUA_FILE_PATH)
        _update_cache()
    except FileNotFoundError:
        print(f"Warning: {LUA_FILE_PATH} not found on initial load. Cache will be empty.")

    # Start background thread
    monitor_thread = threading.Thread(target=_monitor_file_changes, daemon=True)
    monitor_thread.start()
    print("Auction data monitor started in background.")

def get_cached_auction_items():
    """Returns a copy of the auction items from the in-memory cache."""
    with _cache_lock:
        return _auction_data_cache.copy()