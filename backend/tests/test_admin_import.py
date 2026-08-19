from __future__ import annotations

import hashlib
import io
import os
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi import BackgroundTasks, HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.dialects import mysql
from sqlalchemy.schema import CreateIndex, CreateTable

from app import models  # noqa: F401 - registers model metadata
from app.admin_api import (
    ImportRequest,
    _download_archive,
    _extract_single_snapshot,
    _IMPORT_JOBS,
    _IMPORT_LOCK,
    _JOBS_LOCK,
    _public_job,
    _require_admin_token,
    _validate_source_url,
    queue_market_snapshot,
)
from app.database import Base
from app.services.auction_importer import ImportResult


class AdminImportSecurityTest(unittest.TestCase):
    class _FakeResponse(io.BytesIO):
        def __init__(self, content: bytes):
            super().__init__(content)
            self.headers = {"Content-Length": str(len(content))}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def tearDown(self):
        with _JOBS_LOCK:
            _IMPORT_JOBS.clear()
        if _IMPORT_LOCK.locked():
            _IMPORT_LOCK.release()

    def test_admin_token_disabled_missing_and_wrong(self):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="wrong")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as caught:
                _require_admin_token(credentials)
            self.assertEqual(caught.exception.status_code, 503)

        with patch.dict(os.environ, {"IMPORT_ADMIN_TOKEN": "correct"}, clear=True):
            with self.assertRaises(HTTPException) as caught:
                _require_admin_token(None)
            self.assertEqual(caught.exception.status_code, 401)
            with self.assertRaises(HTTPException) as caught:
                _require_admin_token(credentials)
            self.assertEqual(caught.exception.status_code, 401)
            _require_admin_token(HTTPAuthorizationCredentials(scheme="Bearer", credentials="correct"))

    def test_only_tencent_https_storage_hosts_are_allowed(self):
        accepted = (
            "https://bucket-123.cos.ap-shanghai.myqcloud.com/snapshot.tgz?signature=x",
            "https://example.tcb.qcloud.la/snapshot.tgz",
            "https://example.tcloudbaseapp.com/snapshot.tgz",
        )
        for url in accepted:
            self.assertEqual(_validate_source_url(url), url)
        for url in (
            "http://bucket-123.cos.ap-shanghai.myqcloud.com/snapshot.tgz",
            "https://example.com/snapshot.tgz",
            "https://bucket-123.cos.ap-shanghai.myqcloud.com:8443/snapshot.tgz",
            "https://user:pass@bucket-123.cos.ap-shanghai.myqcloud.com/snapshot.tgz",
        ):
            with self.assertRaises(HTTPException, msg=url):
                _validate_source_url(url)

    @staticmethod
    def _make_tgz(path: Path, members: dict[str, bytes]) -> None:
        with tarfile.open(path, "w:gz") as archive:
            for name, content in members.items():
                info = tarfile.TarInfo(name)
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))

    def test_tgz_requires_one_root_auction_lua_and_hashes_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            content = b'AuctionSearchDB = { ["auctions"] = {} }'
            archive = root / "valid.tgz"
            output = root / "auction.lua"
            self._make_tgz(archive, {"auction.lua": content})
            digest = _extract_single_snapshot(archive, output)
            self.assertEqual(digest, hashlib.sha256(content).hexdigest())
            self.assertEqual(output.read_bytes(), content)

            extra = root / "extra.tgz"
            self._make_tgz(extra, {"auction.lua": content, "other.txt": b"no"})
            with self.assertRaises(HTTPException):
                _extract_single_snapshot(extra, output)

            traversal = root / "traversal.tgz"
            self._make_tgz(traversal, {"../auction.lua": content})
            with self.assertRaises(HTTPException):
                _extract_single_snapshot(traversal, output)

    def test_download_streams_to_disk_and_enforces_limit(self):
        content = b"compressed snapshot"
        opener = Mock()
        opener.open.return_value = self._FakeResponse(content)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary) / "snapshot.tgz"
            with patch("app.admin_api.build_opener", return_value=opener):
                downloaded = _download_archive(
                    "https://bucket-123.cos.ap-shanghai.myqcloud.com/snapshot.tgz", destination
                )
            self.assertEqual(downloaded, len(content))
            self.assertEqual(destination.read_bytes(), content)

            too_large = Mock()
            too_large.open.return_value = self._FakeResponse(b"12345")
            with (
                patch("app.admin_api.build_opener", return_value=too_large),
                patch.dict(os.environ, {"MAX_IMPORT_ARCHIVE_BYTES": "4"}),
                self.assertRaises(HTTPException) as caught,
            ):
                _download_archive("https://bucket-123.cos.ap-shanghai.myqcloud.com/large.tgz", destination)
            self.assertEqual(caught.exception.status_code, 413)

    def test_async_job_returns_immediately_rejects_concurrency_and_records_result(self):
        request = ImportRequest(
            sourceUrl="https://bucket-123.cos.ap-shanghai.myqcloud.com/snapshot.tgz",
            expectedSha256="0" * 64,
        )
        background = BackgroundTasks()
        queued = queue_market_snapshot(request, background)
        self.assertEqual(queued["status"], "queued")
        self.assertEqual(_public_job(queued["jobId"])["status"], "queued")
        with self.assertRaises(HTTPException) as caught:
            queue_market_snapshot(request, BackgroundTasks())
        self.assertEqual(caught.exception.status_code, 409)

        imported = ImportResult(
            snapshot_sha256="0" * 64,
            source_size=10,
            source_scan_count=1,
            imported_scan_count=1,
            skipped_scan_count=0,
            imported_listing_count=5,
            existing_listing_count=0,
            duplicate_snapshot=False,
            scan_ids=(1,),
        )
        task = background.tasks[0]
        with (
            patch("app.admin_api._download_archive", return_value=10),
            patch("app.admin_api._extract_single_snapshot", return_value="0" * 64),
            patch("app.admin_api.import_snapshot", return_value=imported),
        ):
            task.func(*task.args, **task.kwargs)
        completed = _public_job(queued["jobId"])
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(completed["result"]["imported_listing_count"], 5)
        self.assertFalse(_IMPORT_LOCK.locked())

        failed_background = BackgroundTasks()
        failed = queue_market_snapshot(request, failed_background)
        task = failed_background.tasks[0]
        with patch("app.admin_api._download_archive", side_effect=RuntimeError("internal details")):
            task.func(*task.args, **task.kwargs)
        failed_status = _public_job(failed["jobId"])
        self.assertEqual(failed_status["status"], "error")
        self.assertFalse(_IMPORT_LOCK.locked())


class MySqlSchemaCompatibilityTest(unittest.TestCase):
    def test_all_tables_and_indexes_compile_for_mysql(self):
        mysql_dialect = mysql.dialect()
        statements: list[str] = []
        for table in Base.metadata.sorted_tables:
            statements.append(str(CreateTable(table).compile(dialect=mysql_dialect)))
            for index in table.indexes:
                statements.append(str(CreateIndex(index).compile(dialect=mysql_dialect)))
        ddl = "\n".join(statements)
        self.assertIn("CREATE TABLE wow_auction_scans", ddl)
        self.assertIn("CREATE TABLE wow_auction_listings", ddl)
        self.assertIn("CREATE TABLE wow_auction_item_summaries", ddl)
        self.assertIn("ix_wow_auction_listings_scan_item", ddl)


if __name__ == "__main__":
    unittest.main()
