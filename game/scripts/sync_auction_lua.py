#!/usr/bin/env python3
"""
将魔兽客户端里的拍卖扫描 SavedVariables 复制到仓库 data/auction.lua，供后端解析与缓存。

游戏内数据保存在（示例）:
  <零售客户端>/_retail_/WTF/Account/<账号数字>/SavedVariables/AuctionSearchDB.lua

与插件 AuctionSearchExample.toc 中 `## SavedVariables: AuctionSearchDB` 一致。

使用方式:
  python game/scripts/sync_auction_lua.py              # 自动发现最新一份并复制
  python game/scripts/sync_auction_lua.py --list       # 仅列出候选文件
  python game/scripts/sync_auction_lua.py -s /path/to/AuctionSearchDB.lua

环境变量（可选）:
  WOW_RETAIL_ROOT   零售客户端根目录（含 _retail_），例如 Windows 下 .../World of Warcraft
  WOW_AUCTION_SV_SRC  直接指定源文件路径，等价于 --source
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

# game/scripts → 仓库根
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DEST = PROJECT_ROOT / "data" / "auction.lua"

SV_NAME = "AuctionSearchDB.lua"
RETAIL_SEG = Path("_retail_")


def _retail_roots_from_env() -> list[Path]:
    roots: list[Path] = []
    env = os.environ.get("WOW_RETAIL_ROOT") or os.environ.get("WOW_RETAIL")
    if env:
        roots.append(Path(env).expanduser().resolve())
    return roots


def _default_retail_roots() -> list[Path]:
    """常见安装路径；用户可用 WOW_RETAIL_ROOT 覆盖。"""
    roots = _retail_roots_from_env()
    if roots:
        return roots
    if sys.platform == "darwin":
        return [Path("/Applications/World of Warcraft")]
    if sys.platform == "win32":
        pf = os.environ.get("PROGRAMFILES(X86)") or os.environ.get("PROGRAMFILES")
        if pf:
            return [Path(pf) / "World of Warcraft"]
        return [Path("C:/Program Files (x86)/World of Warcraft")]
    # Linux 等：常见为 ~/Games 或用户自定义，仅依赖环境变量
    return []


def _iter_sv_candidates() -> list[Path]:
    """枚举 Account/*/SavedVariables/AuctionSearchDB.lua。"""
    found: list[Path] = []
    for base in _default_retail_roots():
        retail = base / RETAIL_SEG if (base / RETAIL_SEG).is_dir() else base
        acc = retail / "WTF" / "Account"
        if not acc.is_dir():
            continue
        for p in acc.glob(f"*/SavedVariables/{SV_NAME}"):
            if p.is_file():
                found.append(p.resolve())
    return found


def _pick_source(candidates: list[Path], account_substr: str | None) -> Path | None:
    if not candidates:
        return None
    if account_substr:
        filtered = [p for p in candidates if account_substr in p.parts[-3]]
        if not filtered:
            return None
        candidates = filtered
    return max(candidates, key=lambda p: p.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="复制 AuctionSearchDB.lua → 仓库 data/auction.lua",
    )
    parser.add_argument(
        "-s",
        "--source",
        type=Path,
        help="源文件路径（默认：自动发现最新修改的 SavedVariables）",
    )
    parser.add_argument(
        "-d",
        "--dest",
        type=Path,
        default=DEFAULT_DEST,
        help=f"目标路径（默认: {DEFAULT_DEST}）",
    )
    parser.add_argument(
        "--account",
        metavar="SUBSTR",
        help="账号文件夹名包含该子串时才选用（多账号时缩小范围）",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出发现的候选源文件后退出（不复制）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只打印将执行的操作，不写入",
    )
    args = parser.parse_args()

    env_src = os.environ.get("WOW_AUCTION_SV_SRC")
    if env_src and not args.source:
        args.source = Path(env_src).expanduser()

    if args.source:
        src = args.source.expanduser().resolve()
        if not src.is_file():
            print(f"错误: 源文件不存在: {src}", file=sys.stderr)
            return 1
    else:
        candidates = _iter_sv_candidates()
        if args.list:
            if not candidates:
                print("未发现候选文件。设置 WOW_RETAIL_ROOT 或使用 --source。")
                return 1
            for p in sorted(candidates, key=lambda x: x.stat().st_mtime, reverse=True):
                mtime = p.stat().st_mtime
                print(f"{mtime:.0f}\t{p}")
            return 0
        src = _pick_source(candidates, args.account)
        if src is None:
            print(
                "错误: 未找到 SavedVariables。请安装零售客户端路径、"
                "设置 WOW_RETAIL_ROOT，或使用 --source 指定 "
                f".../SavedVariables/{SV_NAME}",
                file=sys.stderr,
            )
            return 1

    dest = args.dest.expanduser().resolve()
    if args.list:
        print(f"将选用: {src}")
        return 0

    print(f"源: {src}")
    print(f"目标: {dest}")
    if args.dry_run:
        print("(dry-run，未写入)")
        return 0

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    print(f"已复制 → {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
