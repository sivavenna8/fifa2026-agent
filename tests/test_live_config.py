from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import main
from src.api_client import FixtureAPIClient
from src.config import get_settings, load_env, parse_bool


class LiveConfigurationTests(unittest.TestCase):
    def test_false_selects_football_data_competition_endpoint(self) -> None:
        environment = {
            "USE_SAMPLE_DATA": "false",
            "FOOTBALL_DATA_API_KEY": "test-token",
            "FOOTBALL_DATA_BASE_URL": "https://api.football-data.org/v4/",
            "FOOTBALL_DATA_COMPETITION": "WC",
        }
        with patch.dict(os.environ, environment, clear=True), patch("src.config.load_env"):
            settings = get_settings()
        self.assertFalse(settings.use_sample_data)
        self.assertEqual(settings.api_url, "https://api.football-data.org/v4/competitions/WC/matches")
        self.assertEqual(settings.api_key, "test-token")

    def test_live_mode_never_opens_sample_fixture_file(self) -> None:
        settings = Mock(
            use_sample_data=False,
            strengths_path=Path("data/team_strength.json"),
            fixtures_path=Path("data/sample_fixtures.json"),
            api_url="https://api.football-data.org/v4/competitions/WC/matches",
            api_key="test-token",
            request_timeout=15,
            football_data_competition="WC",
        )
        db = Mock()
        db.upsert_matches.return_value = (1, 0)
        live_match = {
            "id": "123", "stage": "Round of 32", "match_number": 1,
            "status": "scheduled", "home_team": "A", "away_team": "B",
        }
        with (
            patch.object(main, "get_settings", return_value=settings),
            patch.object(main, "load_strengths", return_value={"A": 80, "B": 70}),
            patch.object(main, "load_fixtures") as sample_loader,
            patch.object(FixtureAPIClient, "fetch", return_value=[live_match]),
        ):
            source = main.fetch_data(db)
        sample_loader.assert_not_called()
        db.upsert_matches.assert_called_once_with([live_match])
        db.retain_matches.assert_called_once_with({"123"})
        self.assertIn("football-data.org", source)

    def test_boolean_parser_rejects_ambiguous_values(self) -> None:
        self.assertFalse(parse_bool("false"))
        self.assertTrue(parse_bool("TRUE"))
        self.assertTrue(parse_bool("   ", default=True))
        with self.assertRaises(ValueError):
            parse_bool("sometimes")

    def test_blank_environment_values_use_defaults(self) -> None:
        environment = {
            "REQUEST_TIMEOUT": "   ",
            "USE_SAMPLE_DATA": "",
            "ENABLE_LEAGUE_SCHEDULER": " ",
            "BOOTSTRAP_LEAGUE_DATA": "\t",
            "LEAGUE_SCHEDULE_HOURS_UTC": "  ",
            "FOOTBALL_DATA_BASE_URL": "",
            "FOOTBALL_DATA_COMPETITION": " ",
            "DATABASE_PATH": "",
            "TEAM_STRENGTH_PATH": " ",
            "FIXTURES_PATH": "\t",
            "DATABASE_URL": " ",
            "TELEGRAM_BOT_TOKEN": "",
        }
        with patch.dict(os.environ, environment, clear=True), patch("src.config.load_env"):
            settings = get_settings()
        self.assertEqual(settings.request_timeout, 15)
        self.assertTrue(settings.use_sample_data)
        self.assertFalse(settings.enable_league_scheduler)
        self.assertFalse(settings.bootstrap_league_data)
        self.assertEqual(settings.league_schedule_hours, (8, 20))
        self.assertEqual(settings.football_data_competition, "WC")
        self.assertEqual(settings.database_path.name, "fifa2026.db")
        self.assertIsNone(settings.database_url)
        self.assertIsNone(settings.telegram_token)

    def test_invalid_non_empty_numeric_and_schedule_values_raise_clear_errors(self) -> None:
        with patch.dict(os.environ, {"REQUEST_TIMEOUT": "soon"}, clear=True), patch("src.config.load_env"):
            with self.assertRaisesRegex(ValueError, "REQUEST_TIMEOUT must be an integer"):
                get_settings()
        with patch.dict(
            os.environ, {"LEAGUE_SCHEDULE_HOURS_UTC": "8,noon"}, clear=True
        ), patch("src.config.load_env"):
            with self.assertRaisesRegex(ValueError, "comma-separated integer hours"):
                get_settings()

    def test_runtime_environment_secret_overrides_dotenv_value(self) -> None:
        env_file = Mock()
        env_file.exists.return_value = True
        env_file.read_text.return_value = "FOOTBALL_DATA_API_KEY=file-token\n"
        with patch.dict(os.environ, {"FOOTBALL_DATA_API_KEY": "render-token"}, clear=True):
            load_env(env_file)
            self.assertEqual(os.environ["FOOTBALL_DATA_API_KEY"], "render-token")


if __name__ == "__main__":
    unittest.main()
