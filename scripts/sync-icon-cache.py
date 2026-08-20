#!/usr/bin/env python3
"""Cache every icon missing from Blizzard CDN into the frontend static bundle."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import subprocess
import tempfile
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

OFFICIAL_BASE = "https://render.worldofwarcraft.com/us/icons/56"
FALLBACK_BASE = "https://wow.zamimg.com/images/wow/icons/large"
MAX_ICON_BYTES = 512 * 1024
JPEG_MAGIC = b"\xff\xd8\xff"
WEBP_MAGIC = (b"RIFF", b"WEBP")
FILEDATA_PREFIX = "filedata-"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--icon-map", default="frontend/src/generated/icon-map.json")
    parser.add_argument("--output-dir", default="frontend/public/wow-icons")
    parser.add_argument("--state-file", default="frontend/src/generated/icon-source-status.json")
    parser.add_argument("--local-map", default="frontend/src/generated/local-icon-map.json")
    parser.add_argument("--workers", type=int, default=24)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Recheck icons previously confirmed on the official CDN.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when an icon is unavailable from both sources.",
    )
    return parser.parse_args()


def fetch_jpeg(url: str, timeout: float) -> bytes | None:
    request = Request(url, headers={"User-Agent": "wow-auction-icon-sync/1.0", "Accept": "image/jpeg"})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_ICON_BYTES + 1)
    except (HTTPError, URLError, OSError, TimeoutError):
        return None
    if len(payload) > MAX_ICON_BYTES or not payload.startswith(JPEG_MAGIC):
        return None
    return payload


def write_atomic(destination: Path, payload: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".jpg", delete=False) as output:
            output.write(payload)
            temporary_path = Path(output.name)
        os.replace(temporary_path, destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def write_state(destination: Path, statuses: dict[str, str]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"version": 1, "icons": dict(sorted(statuses.items()))},
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8") + b"\n"
    write_atomic(destination, payload)


def write_local_map(destination: Path, statuses: dict[str, str]) -> None:
    extensions = {
        name: "webp" if status == "static-webp" else "jpg"
        for name, status in statuses.items()
        if status in {"static-jpeg", "static-webp"}
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(dict(sorted(extensions.items())), ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    write_atomic(destination, payload)


def sync_one(
    icon_name: str,
    output_dir: Path,
    timeout: float,
    check_official: bool,
) -> tuple[str, str]:
    destination = output_dir / f"{icon_name}.jpg"
    if destination.is_file() or (output_dir / f"{icon_name}.webp").is_file():
        return "existing", icon_name

    # Synthetic names deliberately have no CDN slug. Report them as a CDN
    # miss immediately so the caller exports the original FileDataID via CASC.
    if icon_name.startswith(FILEDATA_PREFIX):
        return "failed", icon_name

    encoded = quote(icon_name, safe="")
    if check_official and fetch_jpeg(f"{OFFICIAL_BASE}/{encoded}.jpg", timeout) is not None:
        return "official", icon_name

    payload = fetch_jpeg(f"{FALLBACK_BASE}/{encoded}.jpg", timeout)
    if payload is None:
        return "failed", icon_name
    write_atomic(destination, payload)
    return "cached", icon_name


def export_casc_icon(texture_id: str, icon_name: str, output_dir: Path) -> bool:
    destination = output_dir / f"{icon_name}.webp"
    temporary_path = output_dir / f".{icon_name}.{texture_id}.webp"
    command = [
        "npx",
        "--yes",
        "@follenfang/wowdata@0.0.4",
        "icon",
        "export",
        "--file-data-id",
        texture_id,
        "--format",
        "webp",
        "--output",
        str(temporary_path),
        "--source",
        "remote",
        "--region",
        "cn",
        "--product",
        "wow",
        "--build",
        "latest",
        "--locale",
        "zhCN",
    ]
    try:
        result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
        if result.returncode != 0 or not temporary_path.is_file():
            return False
        payload = temporary_path.read_bytes()
        if (
            len(payload) > MAX_ICON_BYTES
            or not payload.startswith(WEBP_MAGIC[0])
            or payload[8:12] != WEBP_MAGIC[1]
        ):
            return False
        os.replace(temporary_path, destination)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def main() -> int:
    args = parse_args()
    icon_map_path = Path(args.icon_map)
    output_dir = Path(args.output_dir)
    state_path = Path(args.state_file)
    local_map_path = Path(args.local_map)
    icon_map = json.loads(icon_map_path.read_text(encoding="utf-8"))
    icon_names = sorted(set(icon_map.values()))
    texture_ids_by_icon: dict[str, list[str]] = {}
    for texture_id, icon_name in icon_map.items():
        texture_ids_by_icon.setdefault(icon_name, []).append(texture_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    statuses: dict[str, str] = {}
    if state_path.is_file():
        state_payload = json.loads(state_path.read_text(encoding="utf-8"))
        if state_payload.get("version") == 1 and isinstance(state_payload.get("icons"), dict):
            statuses = {
                name: status
                for name, status in state_payload["icons"].items()
                if name in icon_names
                and status in {"official", "static-jpeg", "static-webp", "local-jpeg", "local-webp", "missing"}
            }

    pending: list[tuple[str, bool]] = []
    skipped_official = 0
    existing_static = 0
    for icon_name in icon_names:
        if (output_dir / f"{icon_name}.jpg").is_file():
            statuses[icon_name] = "static-jpeg"
            existing_static += 1
        elif (output_dir / f"{icon_name}.webp").is_file():
            statuses[icon_name] = "static-webp"
            existing_static += 1
        elif statuses.get(icon_name) == "official" and not args.refresh:
            skipped_official += 1
        else:
            # New icons check the official CDN first. Previously missing icons,
            # or deleted static files, retry only the two static-fill routes.
            check_official = statuses.get(icon_name) != "missing"
            if statuses.get(icon_name) in {"static-jpeg", "static-webp", "local-jpeg", "local-webp"}:
                check_official = False
            pending.append((icon_name, check_official))

    print(
        f"Plan: total={len(icon_names)} pending={len(pending)} "
        f"known-official={skipped_official} static={existing_static}",
        flush=True,
    )

    counts = {"existing": existing_static, "official": 0, "cached": 0, "failed": 0}
    failures: list[str] = []
    completed = 0
    lock = threading.Lock()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [
            executor.submit(sync_one, name, output_dir, args.timeout, check_official)
            for name, check_official in pending
        ]
        for future in concurrent.futures.as_completed(futures):
            status, icon_name = future.result()
            with lock:
                counts[status] += 1
                if status == "official":
                    statuses[icon_name] = "official"
                elif status in {"cached", "existing"}:
                    statuses[icon_name] = "static-jpeg"
                else:
                    statuses[icon_name] = "missing"
                completed += 1
                if status == "failed":
                    failures.append(icon_name)
                if completed % 250 == 0 or completed == len(pending):
                    print(
                        f"Checked {completed}/{len(pending)}: "
                        f"official={counts['official']} cached={counts['cached']} "
                        f"existing={counts['existing']} failed={counts['failed']}",
                        flush=True,
                    )

    if failures:
        unresolved: list[str] = []
        for icon_name in sorted(failures):
            texture_ids = texture_ids_by_icon.get(icon_name, [])
            exported = any(export_casc_icon(texture_id, icon_name, output_dir) for texture_id in texture_ids)
            if exported:
                counts["failed"] -= 1
                counts["cached"] += 1
                statuses[icon_name] = "static-webp"
                print(f"CASC cached: {icon_name}", flush=True)
            else:
                unresolved.append(icon_name)
        failures = unresolved

    write_state(state_path, {name: statuses[name] for name in icon_names if name in statuses})
    write_local_map(local_map_path, statuses)
    print(f"Updated state: {state_path}", flush=True)

    if failures:
        print("Warning: icons unavailable from both sources (the UI will use its placeholder):")
        for icon_name in sorted(failures):
            print(f"  {icon_name}")
        if args.strict:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
