"""Command-line entry point for importing AuctionSearchExample SavedVariables."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from sqlalchemy.engine import make_url


def describe_database(database_url: str) -> dict[str, str | None]:
    """Return non-secret connection metadata suitable for logs and JSON."""
    parsed_url = make_url(database_url)
    database_name = (
        Path(parsed_url.database).name if parsed_url.get_backend_name() == "sqlite" else parsed_url.database
    )
    return {
        "databaseDialect": parsed_url.get_backend_name(),
        "databaseHost": parsed_url.host,
        "databaseName": database_name,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="校验并导入 WoW 拍卖行 Lua 快照")
    parser.add_argument("source", nargs="?", type=Path, default=Path("data/auction.lua"))
    parser.add_argument("--database-url", help="覆盖 DATABASE_URL（SQLite 或 mysql+pymysql）")
    parser.add_argument("--chunk-size", type=int, default=5000, help="批量插入行数（默认 5000）")
    parser.add_argument("--json", action="store_true", help="仅输出机器可读 JSON")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url

    # DATABASE_URL must be configured before importing app.database.
    from app.database import DATABASE_URL
    from app.services.auction_importer import SnapshotValidationError, import_snapshot

    started = time.perf_counter()
    try:
        result = import_snapshot(args.source, chunk_size=args.chunk_size)
    except (FileNotFoundError, SnapshotValidationError, ValueError, RuntimeError) as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        else:
            print(f"导入失败: {exc}", file=sys.stderr)
        return 1
    elapsed = time.perf_counter() - started
    parsed_url = make_url(DATABASE_URL)
    database_info = describe_database(DATABASE_URL)
    payload = {"ok": True, **database_info, "elapsedSeconds": round(elapsed, 3), **result.to_dict()}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        state = "相同快照，未重复导入" if result.duplicate_snapshot else "导入完成"
        print(f"{state}: {result.snapshot_sha256}")
        print(
            f"scan 新增 {result.imported_scan_count}，跳过 {result.skipped_scan_count}；"
            f"本次新增 listing {result.imported_listing_count}；耗时 {elapsed:.2f}s"
        )
        host_label = f"@{parsed_url.host}" if parsed_url.host else ""
        print(f"数据库: {parsed_url.get_backend_name()}{host_label}/{database_info['databaseName']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
