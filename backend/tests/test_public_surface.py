import os
import unittest
from unittest.mock import patch

from app.database import Base
from app.main import _cors_origins, app
from import_auction import describe_database


class PublicSurfaceTest(unittest.TestCase):
    def test_only_public_market_and_protected_admin_routes_are_registered(self):
        paths = {
            route.path
            for route in app.routes
            if route.path not in {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}
        }
        self.assertEqual(
            paths,
            {
                "/health",
                "/api/market/status",
                "/api/market/catalog",
                "/api/market/items",
                "/api/market/items/{item_id}/listings",
                "/api/market/items/{item_id}/history",
                "/api/icons/{icon_name}.jpg",
                "/api/admin/import",
                "/api/admin/import/{job_id}",
                "/api/admin/reset-market",
            },
        )
        self.assertEqual(
            set(Base.metadata.tables),
            {
                "wow_auction_snapshots",
                "wow_auction_scans",
                "wow_auction_scan_contexts",
                "wow_auction_listings",
                "wow_auction_item_summaries",
                "wow_auction_item_market_scopes",
            },
        )

    def test_cors_uses_configured_origin_plus_local_development(self):
        with patch.dict(os.environ, {"CORS_ORIGINS": "https://example.tcloudbaseapp.com"}):
            origins = _cors_origins()
        self.assertIn("https://example.tcloudbaseapp.com", origins)
        self.assertIn("http://localhost:5173", origins)
        self.assertNotIn("*", origins)

    def test_cli_database_description_never_contains_credentials(self):
        source = "mysql+pymysql://wow_user:super-secret@172.17.0.6:3306/wow_data"
        description = describe_database(source)
        rendered = repr(description)
        self.assertEqual(description["databaseDialect"], "mysql")
        self.assertEqual(description["databaseHost"], "172.17.0.6")
        self.assertEqual(description["databaseName"], "wow_data")
        self.assertNotIn("wow_user", rendered)
        self.assertNotIn("super-secret", rendered)


if __name__ == "__main__":
    unittest.main()
