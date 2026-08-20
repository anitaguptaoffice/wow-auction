"""Protected snapshot ingestion endpoint for a CloudBase private-network database."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import tarfile
import tempfile
import threading
import time
import uuid
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.database import Base, get_db
from app.services.auction_importer import SnapshotValidationError, import_snapshot

router = APIRouter(prefix="/api/admin", tags=["admin"])
bearer = HTTPBearer(auto_error=False)

_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_COS_HOST_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*\.cos\.[a-z0-9-]+\.myqcloud\.com$")
_CLOUDBASE_SUFFIXES = (".tcb.qcloud.la", ".tcloudbaseapp.com", ".cloudbase.net")
_IMPORT_LOCK = threading.Lock()
_JOBS_LOCK = threading.Lock()
_IMPORT_JOBS: dict[str, dict] = {}


class ImportRequest(BaseModel):
    sourceUrl: str = Field(min_length=12, max_length=4096)
    expectedSha256: str = Field(min_length=64, max_length=64)


class ResetMarketRequest(BaseModel):
    confirm: str


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        return None


def _require_admin_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> None:
    configured = os.getenv("IMPORT_ADMIN_TOKEN")
    if not configured:
        raise HTTPException(status_code=503, detail="云端快照导入未启用")
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="缺少导入凭据", headers={"WWW-Authenticate": "Bearer"})
    if not hmac.compare_digest(credentials.credentials, configured):
        raise HTTPException(status_code=401, detail="导入凭据无效", headers={"WWW-Authenticate": "Bearer"})


def _validate_source_url(source_url: str) -> str:
    parsed = urlparse(source_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme != "https" or not host or parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="sourceUrl 必须是无用户信息的 HTTPS URL")
    try:
        port = parsed.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="sourceUrl 端口无效") from exc
    if port not in (None, 443):
        raise HTTPException(status_code=400, detail="sourceUrl 只允许 HTTPS 默认端口")
    allowed = bool(_COS_HOST_RE.fullmatch(host)) or any(host.endswith(suffix) for suffix in _CLOUDBASE_SUFFIXES)
    if not allowed:
        raise HTTPException(status_code=400, detail="sourceUrl 只允许腾讯 COS/CloudBase 对象存储域名")
    return source_url


def _download_archive(source_url: str, destination: Path) -> int:
    max_bytes = int(os.getenv("MAX_IMPORT_ARCHIVE_BYTES", str(64 * 1024 * 1024)))
    max_seconds = float(os.getenv("MAX_IMPORT_DOWNLOAD_SECONDS", "120"))
    started = time.monotonic()
    opener = build_opener(_RejectRedirects())
    request = Request(source_url, headers={"User-Agent": "wow-auction-import/1.0", "Accept": "application/gzip"})
    try:
        with opener.open(request, timeout=60) as response, destination.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length and int(content_length) > max_bytes:
                raise HTTPException(status_code=413, detail="快照压缩包超过大小限制")
            downloaded = 0
            while chunk := response.read(1024 * 1024):
                if time.monotonic() - started > max_seconds:
                    raise HTTPException(status_code=504, detail="对象存储下载超过总时限")
                downloaded += len(chunk)
                if downloaded > max_bytes:
                    raise HTTPException(status_code=413, detail="快照压缩包超过大小限制")
                output.write(chunk)
            return downloaded
    except HTTPException:
        raise
    except HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"对象存储下载失败: HTTP {exc.code}") from exc
    except (URLError, OSError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"对象存储下载失败: {exc}") from exc


def _safe_member_name(name: str) -> bool:
    path = PurePosixPath(name.replace("\\", "/"))
    return not path.is_absolute() and ".." not in path.parts


def _extract_single_snapshot(archive: Path, destination: Path) -> str:
    max_bytes = int(os.getenv("MAX_IMPORT_LUA_BYTES", str(256 * 1024 * 1024)))
    try:
        with tarfile.open(archive, mode="r:gz") as bundle:
            members = bundle.getmembers()
            if any(not _safe_member_name(member.name) for member in members):
                raise HTTPException(status_code=400, detail="压缩包包含不安全路径")
            files = [member for member in members if member.isfile()]
            non_dirs = [member for member in members if not member.isfile() and not member.isdir()]
            if non_dirs or len(files) != 1 or files[0].name != "auction.lua":
                raise HTTPException(status_code=400, detail="tgz 必须且只能包含根目录下的 auction.lua")
            member = files[0]
            if member.size > max_bytes:
                raise HTTPException(status_code=413, detail="解压后的 auction.lua 超过大小限制")
            source = bundle.extractfile(member)
            if source is None:
                raise HTTPException(status_code=400, detail="无法读取压缩包中的 auction.lua")
            digest = hashlib.sha256()
            written = 0
            with source, destination.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    written += len(chunk)
                    if written > max_bytes:
                        raise HTTPException(status_code=413, detail="解压后的 auction.lua 超过大小限制")
                    digest.update(chunk)
                    output.write(chunk)
            if written != member.size:
                raise HTTPException(status_code=400, detail="auction.lua 解压字节数不一致")
            return digest.hexdigest()
    except HTTPException:
        raise
    except (tarfile.TarError, EOFError, OSError) as exc:
        raise HTTPException(status_code=400, detail=f"无效的 tgz 压缩包: {exc}") from exc


def _update_job(job_id: str, **values) -> None:
    with _JOBS_LOCK:
        job = _IMPORT_JOBS.get(job_id)
        if job is not None:
            job.update(values)


def _public_job(job_id: str) -> dict:
    with _JOBS_LOCK:
        job = _IMPORT_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="导入任务不存在或已过期")
        return dict(job)


def _safe_job_error(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return str(exc.detail)[:500]
    if isinstance(exc, SnapshotValidationError):
        return f"快照完整性校验失败: {exc}"[:500]
    if isinstance(exc, (ValueError, RuntimeError)):
        return f"快照入库失败: {exc}"[:500]
    return "导入任务发生内部错误"


def _run_import_job(job_id: str, source_url: str, expected_sha256: str) -> None:
    started = time.perf_counter()
    _update_job(job_id, status="running", startedAt=time.time())
    try:
        with tempfile.TemporaryDirectory(prefix="wow-auction-import-") as temporary:
            temp_dir = Path(temporary)
            archive = temp_dir / "snapshot.tgz"
            snapshot = temp_dir / "auction.lua"
            archive_bytes = _download_archive(source_url, archive)
            actual_sha = _extract_single_snapshot(archive, snapshot)
            if not hmac.compare_digest(actual_sha.lower(), expected_sha256.lower()):
                raise HTTPException(status_code=400, detail="auction.lua SHA-256 不匹配")
            result = import_snapshot(snapshot)
        _update_job(
            job_id,
            status="complete",
            completedAt=time.time(),
            archiveBytes=archive_bytes,
            elapsedSeconds=round(time.perf_counter() - started, 3),
            result=result.to_dict(),
        )
    except Exception as exc:
        _update_job(
            job_id,
            status="error",
            completedAt=time.time(),
            elapsedSeconds=round(time.perf_counter() - started, 3),
            error=_safe_job_error(exc),
        )
    finally:
        _IMPORT_LOCK.release()


@router.post("/import", status_code=202, dependencies=[Depends(_require_admin_token)])
def queue_market_snapshot(request: ImportRequest, background_tasks: BackgroundTasks):
    source_url = _validate_source_url(request.sourceUrl)
    if not _SHA256_RE.fullmatch(request.expectedSha256):
        raise HTTPException(status_code=422, detail="expectedSha256 必须是 64 位十六进制 SHA-256")
    if not _IMPORT_LOCK.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="已有快照导入任务正在执行")

    job_id = uuid.uuid4().hex
    now = time.time()
    with _JOBS_LOCK:
        completed = [key for key, value in _IMPORT_JOBS.items() if value.get("status") in {"complete", "error"}]
        for old_job_id in completed[:-19]:
            _IMPORT_JOBS.pop(old_job_id, None)
        _IMPORT_JOBS[job_id] = {"jobId": job_id, "status": "queued", "queuedAt": now}
    try:
        background_tasks.add_task(
            _run_import_job,
            job_id,
            source_url,
            request.expectedSha256.lower(),
        )
    except Exception:
        with _JOBS_LOCK:
            _IMPORT_JOBS.pop(job_id, None)
        _IMPORT_LOCK.release()
        raise
    return {"jobId": job_id, "status": "queued"}


@router.get("/import/{job_id}", dependencies=[Depends(_require_admin_token)])
def read_import_job(job_id: str):
    return _public_job(job_id)


@router.post("/reset-market", dependencies=[Depends(_require_admin_token)])
def reset_market_data(request: ResetMarketRequest, db: Session = Depends(get_db)):
    """Delete only this project's auction namespace before a deliberate full reload."""
    if request.confirm != "DELETE_WOW_AUCTION_MARKET_DATA":
        raise HTTPException(status_code=422, detail="confirm 不匹配，拒绝清理")
    counts = {
        "snapshots": int(db.scalar(select(func.count()).select_from(models.AuctionSnapshot)) or 0),
        "scans": int(db.scalar(select(func.count()).select_from(models.AuctionScan)) or 0),
        "listings": int(db.scalar(select(func.count()).select_from(models.AuctionListing)) or 0),
    }
    try:
        bind = db.get_bind()
        db.close()
        Base.metadata.drop_all(bind=bind)
        Base.metadata.create_all(bind=bind)
    except Exception:
        raise
    return {"cleared": True, **counts}
