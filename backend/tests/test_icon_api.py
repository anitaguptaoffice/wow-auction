from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.icon_api import read_cached_icon


class IconFallbackTest(unittest.TestCase):
    def test_rejects_names_outside_generated_manifest(self):
        with self.assertRaises(HTTPException) as raised:
            read_cached_icon("not_a_real_icon")
        self.assertEqual(raised.exception.status_code, 404)

    def test_downloads_once_and_reuses_disk_cache(self):
        icon_name = "inv_12_profession_alchemy_lightpotion_purple"
        jpeg = b"\xff\xd8\xff" + b"cached-icon"
        with tempfile.TemporaryDirectory() as temporary, patch.dict(
            "os.environ", {"ICON_CACHE_DIR": temporary}
        ), patch("app.icon_api._download_icon", return_value=jpeg) as download:
            first = read_cached_icon(icon_name)
            second = read_cached_icon(icon_name)

        self.assertEqual(download.call_count, 1)
        self.assertEqual(Path(first.path).name, f"{icon_name}.jpg")
        self.assertEqual(Path(second.path).name, f"{icon_name}.jpg")
        self.assertEqual(first.headers["cache-control"], "public, max-age=31536000, immutable")
