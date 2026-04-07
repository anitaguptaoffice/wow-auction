#!/usr/bin/env python3
"""
Raidbots 静态数据同步脚本

从 https://www.raidbots.com/static/data/live/ 下载与拍卖行项目相关的游戏数据文件。
这些数据用于将拍卖行中的物品 ID、Bonus ID 等翻译为人类可读的信息。

使用方式:
    python game/scripts/sync_raidbots.py              # 一次性同步
    python game/scripts/sync_raidbots.py --schedule   # 定时同步（每6小时）
    python game/scripts/sync_raidbots.py --force      # 强制覆盖（忽略 hash 检查）
    python game/scripts/sync_raidbots.py --list       # 列出所有可下载文件
"""

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

# ──────────────────── 配置 ────────────────────

BASE_URL = "https://www.raidbots.com/static/data/live"
METADATA_URL = f"{BASE_URL}/metadata.json"

# 项目根目录和数据存储目录（game/scripts → 仓库根）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "raidbots"

# 定时同步间隔（秒），默认 6 小时
DEFAULT_INTERVAL = 6 * 60 * 60

# 请求超时时间（秒）
REQUEST_TIMEOUT = 60

# User-Agent
USER_AGENT = "wow-auction/0.1.0 (Raidbots Static Data Sync)"

# ──── 对拍卖行项目有用的文件清单 ────
#
# 优先级说明:
#   ★★★ 核心 — 拍卖行数据展示/解析直接依赖
#   ★★☆ 重要 — 增强数据丰富度
#   ★☆☆ 可选 — 辅助功能
#
SYNC_FILES: dict[str, dict] = {
    # ★★★ 核心: 物品信息
    "equippable-items.json": {
        "description": "可装备物品列表 (ID/名称/品质/装等/类型/图标)",
        "priority": "core",
    },
    "item-names.json": {
        "description": "物品多语言名称 (中/英/韩/德/法等)",
        "priority": "core",
    },
    "bonuses.json": {
        "description": "Bonus ID 映射 (词缀/品质/装等调整/属性)",
        "priority": "core",
    },
    "icon-lookup.json": {
        "description": "物品/法术图标 ID→文件名映射",
        "priority": "core",
    },

    # ★★☆ 重要: 装备增强 & 副本来源
    "enchantments.json": {
        "description": "附魔数据 (名称/属性/装备限制)",
        "priority": "important",
    },
    "gems.json": {
        "description": "宝石数据 (属性/颜色/品质)",
        "priority": "important",
    },
    "instances.json": {
        "description": "副本/团本数据 (名称/Boss/难度)",
        "priority": "important",
    },
    "item-curves.json": {
        "description": "物品等级曲线 (装等缩放计算)",
        "priority": "important",
    },

    # ★★☆ 重要: 物品关联数据
    "item-sets.json": {
        "description": "套装数据 (套装效果/包含物品)",
        "priority": "important",
    },
    "item-conversions.json": {
        "description": "物品转化数据 (催化器等)",
        "priority": "important",
    },
    "crafting.json": {
        "description": "制造数据 (配方/材料/制造品质)",
        "priority": "important",
    },

    # ★☆☆ 可选: 辅助功能
    "item-limit-categories.json": {
        "description": "物品限制分类 (唯一装备等)",
        "priority": "optional",
    },
    "instance-names.json": {
        "description": "副本多语言名称",
        "priority": "optional",
    },
    "encounter-names.json": {
        "description": "Boss 多语言名称",
        "priority": "optional",
    },
    "encounter-items.json": {
        "description": "Boss 掉落物品列表",
        "priority": "optional",
    },
    "seasons.json": {
        "description": "赛季信息",
        "priority": "optional",
    },
}

# ──────────────────── 日志 ────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("sync_raidbots")

# ──────────────────── 工具函数 ────────────────────


def _override_data_dir(new_dir: Path) -> None:
    """覆盖数据存储目录。"""
    global DATA_DIR
    DATA_DIR = new_dir


def fetch_url(url: str) -> bytes:
    """下载 URL 内容，返回 bytes。"""
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return resp.read()
    except HTTPError as e:
        log.error("HTTP 错误 %d: %s", e.code, url)
        raise
    except URLError as e:
        log.error("网络错误: %s - %s", e.reason, url)
        raise


def file_md5(path: Path) -> str | None:
    """计算本地文件的 MD5 哈希。"""
    if not path.exists():
        return None
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def content_md5(data: bytes) -> str:
    """计算 bytes 数据的 MD5 哈希。"""
    return hashlib.md5(data).hexdigest()


def save_file(path: Path, data: bytes) -> None:
    """保存数据到文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def load_sync_state() -> dict:
    """读取同步状态文件。"""
    state_file = DATA_DIR / ".sync_state.json"
    if state_file.exists():
        with open(state_file, "r") as f:
            return json.load(f)
    return {}


def save_sync_state(state: dict) -> None:
    """保存同步状态。"""
    state_file = DATA_DIR / ".sync_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, "w") as f:
        json.dump(state, f, indent=2)


# ──────────────────── 核心逻辑 ────────────────────


def fetch_metadata() -> dict:
    """获取 Raidbots 元数据，包含版本和文件列表。"""
    log.info("获取元数据: %s", METADATA_URL)
    data = fetch_url(METADATA_URL)
    metadata = json.loads(data)
    log.info(
        "当前版本: %s (build: %s, hash: %s)",
        metadata.get("environment", "unknown"),
        metadata.get("wowBuild", "unknown"),
        metadata.get("contentHash", "unknown"),
    )
    return metadata


def sync_files(force: bool = False, priority_filter: str | None = None) -> dict:
    """
    同步所有配置的数据文件。

    Args:
        force: 强制重新下载，忽略哈希检查。
        priority_filter: 只下载指定优先级的文件 (core/important/optional)。

    Returns:
        同步结果统计。
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state = load_sync_state()
    stats = {"downloaded": 0, "skipped": 0, "failed": 0, "total": 0}

    # 先获取元数据
    try:
        metadata = fetch_metadata()
        content_hash = metadata.get("contentHash", "")
    except Exception:
        log.warning("无法获取元数据，将尝试直接下载文件")
        metadata = {}
        content_hash = ""

    # 保存元数据
    if metadata:
        save_file(DATA_DIR / "metadata.json", json.dumps(metadata, indent=2).encode())

    # 检查是否有新版本
    last_hash = state.get("last_content_hash", "")
    if content_hash and content_hash == last_hash and not force:
        log.info("数据未更新 (hash: %s)，跳过同步", content_hash[:12])
        stats["skipped"] = len(SYNC_FILES)
        stats["total"] = len(SYNC_FILES)
        return stats

    # 逐个下载文件
    files_to_sync = {
        name: info
        for name, info in SYNC_FILES.items()
        if priority_filter is None or info["priority"] == priority_filter
    }

    for filename, info in files_to_sync.items():
        stats["total"] += 1
        url = f"{BASE_URL}/{filename}"
        dest = DATA_DIR / filename

        try:
            log.info("下载 [%s] %s - %s", info["priority"], filename, info["description"])
            data = fetch_url(url)

            # 哈希比对，避免无意义写入
            new_hash = content_md5(data)
            old_hash = file_md5(dest)

            if new_hash == old_hash and not force:
                log.info("  └─ 文件无变化，跳过")
                stats["skipped"] += 1
                continue

            save_file(dest, data)
            size_kb = len(data) / 1024
            log.info("  └─ 已保存 (%.1f KB)", size_kb)
            stats["downloaded"] += 1

            # 更新状态
            state[f"file_{filename}_hash"] = new_hash
            state[f"file_{filename}_size"] = len(data)

        except Exception as e:
            log.error("  └─ 下载失败: %s", e)
            stats["failed"] += 1

    # 更新同步状态
    state["last_content_hash"] = content_hash
    state["last_sync_time"] = datetime.now(timezone.utc).isoformat()
    state["last_wow_build"] = metadata.get("wowBuild", "unknown")
    save_sync_state(state)

    log.info(
        "同步完成: 下载 %d, 跳过 %d, 失败 %d / 共 %d",
        stats["downloaded"],
        stats["skipped"],
        stats["failed"],
        stats["total"],
    )
    return stats


def list_files() -> None:
    """列出所有可同步的文件及其状态。"""
    state = load_sync_state()
    print("\n📦 Raidbots 可同步数据文件列表\n")
    print(f"{'文件名':<32} {'优先级':<10} {'本地状态':<12} 说明")
    print("─" * 100)

    for filename, info in SYNC_FILES.items():
        dest = DATA_DIR / filename
        if dest.exists():
            size_kb = dest.stat().st_size / 1024
            status = f"✅ {size_kb:.0f}KB"
        else:
            status = "❌ 未下载"

        priority_icon = {"core": "★★★", "important": "★★☆", "optional": "★☆☆"}.get(
            info["priority"], "   "
        )
        print(f"{filename:<32} {priority_icon:<10} {status:<12} {info['description']}")

    last_sync = state.get("last_sync_time", "从未同步")
    last_build = state.get("last_wow_build", "未知")
    print(f"\n上次同步: {last_sync}")
    print(f"游戏版本: {last_build}\n")


def run_scheduled(interval: int, force: bool = False) -> None:
    """定时运行同步任务。"""
    log.info("启动定时同步，间隔 %d 秒 (%.1f 小时)", interval, interval / 3600)

    while True:
        try:
            log.info("=" * 50)
            log.info("开始定时同步...")
            sync_files(force=force)
        except KeyboardInterrupt:
            log.info("收到中断信号，退出定时同步")
            sys.exit(0)
        except Exception as e:
            log.error("同步出错: %s", e)

        log.info("下次同步: %d 秒后", interval)
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            log.info("收到中断信号，退出定时同步")
            sys.exit(0)


# ──────────────────── CLI 入口 ────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Raidbots 静态数据同步工具 — 下载魔兽世界物品/装备/词缀等数据",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python game/scripts/sync_raidbots.py              # 同步所有文件
  python game/scripts/sync_raidbots.py --force      # 强制重新下载
  python game/scripts/sync_raidbots.py --priority core  # 只下载核心文件
  python game/scripts/sync_raidbots.py --schedule   # 每 6 小时自动同步
  python game/scripts/sync_raidbots.py --list       # 查看文件列表和状态
        """,
    )
    parser.add_argument(
        "--schedule",
        action="store_true",
        help="启用定时同步模式",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help=f"定时同步间隔（秒），默认 {DEFAULT_INTERVAL}（6小时）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新下载所有文件（忽略 hash 检查）",
    )
    parser.add_argument(
        "--priority",
        choices=["core", "important", "optional"],
        default=None,
        help="只下载指定优先级的文件",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出所有可同步文件及其状态",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        help=f"数据存储目录，默认 {DATA_DIR}",
    )

    args = parser.parse_args()

    # 自定义数据目录
    if args.data_dir:
        _override_data_dir(Path(args.data_dir))

    if args.list:
        list_files()
        return

    if args.schedule:
        run_scheduled(interval=args.interval, force=args.force)
    else:
        sync_files(force=args.force, priority_filter=args.priority)


if __name__ == "__main__":
    main()
