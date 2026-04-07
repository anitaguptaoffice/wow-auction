"""仓库根目录与数据路径（相对 `backend/app` 定位，不依赖进程 cwd）。"""

from pathlib import Path

# backend/app/config.py → 仓库根目录
ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
AUCTION_LUA_PATH = DATA_DIR / "auction.lua"
DATABASE_PATH = DATA_DIR / "wow-auction.db"
