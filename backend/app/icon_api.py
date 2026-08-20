"""Restricted icon fallback proxy with an instance-local disk cache."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(prefix="/api/icons", tags=["icons"])

_ICON_MAP_PATH = Path(__file__).with_name("icon-map.json")
_ALLOWED_ICON_NAMES = frozenset(json.loads(_ICON_MAP_PATH.read_text(encoding="utf-8")).values())
_UPSTREAM_BASE = "https://wow.zamimg.com/images/wow/icons/large"
_MAX_ICON_BYTES = 512 * 1024
_CACHE_HEADERS = {"Cache-Control": "public, max-age=31536000, immutable"}


def _cache_dir() -> Path:
    configured = os.getenv("ICON_CACHE_DIR")
    local_static_cache = Path("frontend/public/wow-icons")
    default = local_static_cache if local_static_cache.parent.is_dir() else Path("data/icon-cache")
    path = Path(configured) if configured else default
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_icon(icon_name: str) -> bytes:
    request = Request(
        f"{_UPSTREAM_BASE}/{quote(icon_name, safe='')}.jpg",
        headers={"User-Agent": "wow-auction-icon-cache/1.0", "Accept": "image/jpeg"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            content_type = response.headers.get_content_type()
            if content_type not in {"image/jpeg", "image/jpg"}:
                raise HTTPException(status_code=502, detail="备用图标源返回了非 JPEG 内容")
            payload = response.read(_MAX_ICON_BYTES + 1)
    except HTTPError as exc:
        status = 404 if exc.code in {403, 404} else 502
        raise HTTPException(status_code=status, detail="备用图标源没有该资源") from exc
    except (URLError, OSError, TimeoutError) as exc:
        raise HTTPException(status_code=502, detail="备用图标源暂时不可用") from exc
    if len(payload) > _MAX_ICON_BYTES or not payload.startswith(b"\xff\xd8\xff"):
        raise HTTPException(status_code=502, detail="备用图标内容校验失败")
    return payload


@router.get("/{icon_name}.jpg", response_class=FileResponse)
def read_cached_icon(icon_name: str):
    if icon_name not in _ALLOWED_ICON_NAMES:
        raise HTTPException(status_code=404, detail="未知图标")

    destination = _cache_dir() / f"{icon_name}.jpg"
    if not destination.is_file():
        payload = _download_icon(icon_name)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".jpg", delete=False) as output:
                output.write(payload)
                temporary_path = Path(output.name)
            os.replace(temporary_path, destination)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    return FileResponse(destination, media_type="image/jpeg", headers=_CACHE_HEADERS)
