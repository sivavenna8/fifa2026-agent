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
        with self.assertRaises(ValueError):
            parse_bool("sometimes")

    def test_runtime_environment_secret_overrides_dotenv_value(self) -> None:
        env_file = Mock()
        env_file.exists.return_value = True
        env_file.read_text.return_value = "FOOTBALL_DATA_API_KEY=file-token\n"
        with patch.dict(os.environ, {"FOOTBALL_DATA_API_KEY": "render-token"}, clear=True):
            load_env(env_file)
            self.assertEqual(os.environ["FOOTBALL_DATA_API_KEY"], "render-token")


if __name__ == "__main__":
    unittest.main()
