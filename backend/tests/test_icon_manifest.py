from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class IconManifestTest(unittest.TestCase):
    def test_covers_interface_housing_and_unnamed_file_data_ids(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "auction.db"
            listfile = root / "community-listfile.csv"
            frontend_map = root / "frontend-map.json"
            backend_map = root / "backend-map.json"

            # sqlite3.Connection.__exit__ commits but does not close the handle;
            # explicitly close it so Windows can remove the temporary database.
            with closing(sqlite3.connect(database)) as connection:
                connection.execute("CREATE TABLE wow_auction_item_summaries (texture INTEGER)")
                connection.executemany(
                    "INSERT INTO wow_auction_item_summaries (texture) VALUES (?)",
                    [(101,), (102,), (103,)],
                )
                connection.commit()
            listfile.write_text(
                "101;interface/icons/inv_known.blp\n"
                "102;housing/icons/inv_housing.blp\n",
                encoding="utf-8",
            )

            subprocess.run(
                [
                    sys.executable,
                    "scripts/generate_icon_manifest.py",
                    str(database),
                    str(listfile),
                    str(frontend_map),
                    str(backend_map),
                ],
                cwd=REPOSITORY_ROOT,
                check=True,
                capture_output=True,
                text=True,
            )

            expected = {
                "101": "inv_known",
                "102": "inv_housing",
                "103": "filedata-103",
            }
            self.assertEqual(json.loads(frontend_map.read_text(encoding="utf-8")), expected)
            self.assertEqual(json.loads(backend_map.read_text(encoding="utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
