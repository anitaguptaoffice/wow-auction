#!/usr/bin/env python3
"""Generate frontend/backend icon allowlists from an auction SQLite database."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("database", nargs="?", default="data/wow-auction.db")
    parser.add_argument("listfile")
    parser.add_argument("output", nargs="?", default="frontend/src/generated/icon-map.json")
    parser.add_argument("backend_output", nargs="?", default="backend/app/icon-map.json")
    return parser.parse_args()


def load_texture_ids(database: Path) -> list[int]:
    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT DISTINCT texture FROM wow_auction_item_summaries "
            "WHERE texture IS NOT NULL ORDER BY texture"
        ).fetchall()
    return [int(row[0]) for row in rows]


def load_icon_names(listfile: Path, requested: set[int]) -> dict[int, str]:
    resolved: dict[int, str] = {}
    for raw_line in listfile.read_text(encoding="utf-8-sig").splitlines():
        texture_text, separator, icon_path = raw_line.partition(";")
        if not separator:
            continue
        try:
            texture_id = int(texture_text)
        except ValueError:
            continue
        normalized = icon_path.replace("\\", "/")
        match = re.fullmatch(r"(?:interface|housing)/icons/([^/]+)\.blp", normalized)
        if texture_id not in requested or not match:
            continue
        resolved[texture_id] = re.sub(r"\s", "-", match.group(1))
    return resolved


def write_manifest(destination: Path, manifest: dict[str, str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    args = parse_args()
    database = Path(args.database)
    listfile = Path(args.listfile)
    output = Path(args.output)
    backend_output = Path(args.backend_output)

    if not database.is_file():
        raise SystemExit(f"Database not found: {database}")
    if not listfile.is_file():
        raise SystemExit(f"Listfile not found: {listfile}")

    texture_ids = load_texture_ids(database)
    resolved = load_icon_names(listfile, set(texture_ids))
    manifest = {
        str(texture_id): resolved.get(texture_id, f"filedata-{texture_id}")
        for texture_id in texture_ids
    }
    write_manifest(output, manifest)
    write_manifest(backend_output, manifest)

    synthetic_count = sum(name.startswith("filedata-") for name in manifest.values())
    print(
        f"Generated {output} ({len(manifest)}/{len(texture_ids)} textures covered; "
        f"{synthetic_count} use FileDataID fallback)"
    )
    print(f"Mirrored icon allowlist to {backend_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
