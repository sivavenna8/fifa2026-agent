from __future__ import annotations

import unittest
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from src.league_config import get_league
from src.web_app import _prepare_league_match


ROOT = Path(__file__).resolve().parent.parent


def prediction_match(**overrides):
    match = {
        "id": "1",
        "status": "scheduled",
        "home_team": "Manchester City FC",
        "away_team": "Bournemouth FC",
        "home_score": None,
        "away_score": None,
        "kickoff_uk": "24 AUG · 15:00 UK",
        "home_probability": 0.534,
        "draw_probability": 0.330,
        "away_probability": 0.136,
        "predicted_outcome": "H",
        "confidence": "Medium",
        "correct": None,
        "prediction_status": "provisional",
        "locked_at": "",
    }
    match.update(overrides)
    return _prepare_league_match(match)


class LeagueDashboardRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.template = Environment(loader=FileSystemLoader(ROOT / "templates")).get_template("league_dashboard.html")

    def render(self, matches, backtest=None):
        today = date.today()
        return self.template.render(
            league=get_league("PL"),
            leagues=[get_league("PL")],
            selected_date=today.isoformat(),
            display_date=today.strftime("%d %b %Y").upper(),
            previous_date=(today - timedelta(days=1)).isoformat(),
            next_date=(today + timedelta(days=1)).isoformat(),
            today=today.isoformat(),
            yesterday=(today - timedelta(days=1)).isoformat(),
            tomorrow=(today + timedelta(days=1)).isoformat(),
            next_matchday=None,
            matches=matches,
            standings=[{"position": 1, "team": "Arsenal FC", "played": 3, "won": 3, "drawn": 0, "lost": 0, "goal_difference": 7, "points": 9}],
            metrics={"total": 0, "correct": 0, "accuracy": None, "high_confidence_accuracy": None, "last_10_correct": 0, "last_10_total": 0},
            backtest=backtest,
            last_updated=None,
        )

    def test_future_provisional_and_locked_states(self):
        provisional = self.render([prediction_match()])
        self.assertIn("Provisional Prediction", provisional)
        self.assertNotIn("Finished · FT", provisional)

        locked = self.render([prediction_match(prediction_status="locked", locked_at="2026-08-23T12:00:00Z")])
        self.assertIn("Locked · Official Agent Pick", locked)
        self.assertNotIn("Finished · FT", locked)

    def test_completed_correct_uses_original_probabilities(self):
        html = self.render([prediction_match(status="completed", home_score=2, away_score=0)])
        self.assertIn("Finished · FT", html)
        self.assertNotIn("Provisional Prediction", html)
        self.assertIn("Original prediction", html)
        self.assertIn("53.4%", html)
        self.assertIn("✓ Correct", html)

    def test_completed_incorrect_locked_pick_is_official(self):
        html = self.render([prediction_match(status="completed", home_score=0, away_score=1, prediction_status="evaluated", locked_at="2026-08-22T12:00:00Z", correct=False)])
        self.assertIn("Finished · FT", html)
        self.assertIn("Official locked prediction", html)
        self.assertIn("× Incorrect", html)
        self.assertNotIn("Provisional Prediction", html)

    def test_standings_use_one_semantic_table(self):
        html = self.render([])
        self.assertIn('<table class="league-table">', html)
        self.assertIn("<colgroup>", html)
        self.assertIn('<th scope="col">Pos</th>', html)
        self.assertIn('<th scope="row">Arsenal</th>', html)
        self.assertNotIn('class="table-row', html)

    def test_public_copy_hides_exact_model_and_feature_details(self):
        backtest = {
            "model_name": "logistic-regression",
            "metrics": {
                "samples": 1000,
                "test_size": 150,
                "models": {"logistic-regression": {"test": {"accuracy": 0.55, "log_loss": 0.98}}},
            },
        }
        html = self.render([], backtest=backtest)
        self.assertIn("SportsIntelAI ML Engine", html)
        self.assertIn("Historical validation", html)
        self.assertIn("Historical matches analysed", html)
        self.assertIn("Held-out test matches", html)
        self.assertIn("48-hour lock window", html)
        self.assertNotIn("Logistic Regression", html)
        self.assertNotIn("Production model", html)
        self.assertNotIn(">Elo<", html)
        self.assertNotIn(">Goals<", html)
        self.assertNotIn(">Rest<", html)


if __name__ == "__main__":
    unittest.main()
