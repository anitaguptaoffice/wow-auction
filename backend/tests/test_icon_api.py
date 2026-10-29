from __future__ import annotations

import tempfile
import unittest
from io import BytesIO
from email.message import Message
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from app.icon_api import read_cached_icon, _download_icon


class IconFallbackTest(unittest.TestCase):
    def test_upstream_request_is_an_http_request_and_resolves_numeric_ids(self):
        jpeg = b'\xff\xd8\xfficon'
        response = BytesIO(jpeg)
        response.headers = Message()
        response.headers['Content-Type'] = 'image/jpeg'
        with patch('app.icon_api.urlopen', return_value=response) as download, patch('app.icon_api.ICON_NAMES', {}):
            self.assertEqual(_download_icon('filedata-937859'), jpeg)
        request = download.call_args.args[0]
        self.assertEqual(request.full_url, 'https://wow.zamimg.com/images/wow/icons/large/937859.jpg')
        self.assertEqual(request.get_header('Accept'), 'image/jpeg')

    def test_resolves_old_placeholder_to_known_icon_name(self):
        jpeg = b'\xff\xd8\xfficon'
        response = BytesIO(jpeg)
        response.headers = Message()
        response.headers['Content-Type'] = 'image/jpeg'
        with patch('app.icon_api.urlopen', return_value=response) as download, patch(
            'app.icon_api.ICON_NAMES', {'937859': 'inv_helm_cloth_draenorquest90_b_01'}
        ):
            self.assertEqual(_download_icon('filedata-937859'), jpeg)
        self.assertTrue(download.call_args.args[0].full_url.endswith('/inv_helm_cloth_draenorquest90_b_01.jpg'))

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
