from __future__ import annotations

import json
import unittest
from pathlib import Path

from fastapi import FastAPI

from app import app
from scripts.migrate_sqlite_to_postgres import TABLES
from src.database import POSTGRES_SCHEMA, Database, _PostgresConnection
from src.league_model import LeagueModel


ROOT = Path(__file__).resolve().parent.parent


class DeploymentTests(unittest.TestCase):
    def test_vercel_entrypoint_exports_fastapi_app(self):
        self.assertIsInstance(app, FastAPI)
        registered_paths = {route.path for route in app.routes}
        self.assertTrue(
            {
                "/",
                "/league",
                "/fifa-2026",
                "/health",
                "/api/leagues/{league}/matches",
                "/api/leagues/{league}/standings",
                "/api/leagues/{league}/performance",
                "/static",
            }.issubset(registered_paths)
        )

    def test_committed_production_model_loads(self):
        model = LeagueModel.load(ROOT / "models" / "pl_model.pkl")
        self.assertTrue(model.model_name)

    def test_vercel_configuration_is_valid_and_routes_to_entrypoint(self):
        config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
        self.assertNotIn("rewrites", config)
        self.assertIn("models/**", config["functions"]["app.py"]["includeFiles"])
        self.assertIn("templates/**", config["functions"]["app.py"]["includeFiles"])
        self.assertIn("static/**", config["functions"]["app.py"]["includeFiles"])

    def test_postgres_adapter_translates_placeholders(self):
        self.assertEqual(
            _PostgresConnection._sql("SELECT * FROM matches WHERE id=? AND status=?"),
            "SELECT * FROM matches WHERE id=%s AND status=%s",
        )

    def test_postgres_schema_avoids_sqlite_only_ddl(self):
        self.assertNotIn("AUTOINCREMENT", POSTGRES_SCHEMA)
        self.assertNotIn("PRAGMA", POSTGRES_SCHEMA)
        self.assertIn("BIGSERIAL", POSTGRES_SCHEMA)

    def test_database_url_selects_postgres_without_touching_local_sqlite(self):
        database = Database(
            ROOT / "data" / "must-not-be-created.db",
            "postgresql://example.invalid/sportsintel",
            initialize_schema=False,
        )
        self.assertEqual(database.backend, "postgres")
        self.assertFalse((ROOT / "data" / "must-not-be-created.db").exists())

    def test_migration_covers_every_application_table(self):
        expected = {
            "teams",
            "matches",
            "agent_runs",
            "predictions",
            "bracket_snapshots",
            "leagues",
            "league_matches",
            "league_standings",
            "league_predictions",
            "team_ratings",
            "model_metrics",
        }
        self.assertEqual(set(TABLES), expected)


if __name__ == "__main__":
    unittest.main()
