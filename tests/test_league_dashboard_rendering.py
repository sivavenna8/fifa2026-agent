from __future__ import annotations

import unittest
import re
from copy import deepcopy
from datetime import date, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from fastapi.testclient import TestClient

from app import app
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
            standings=[
                {"position": 1, "team": "Manchester City FC", "played": 3, "won": 3, "drawn": 0, "lost": 0, "goal_difference": 7, "points": 9},
                {"position": 2, "team": "Arsenal FC", "played": 3, "won": 2, "drawn": 1, "lost": 0, "goal_difference": 4, "points": 7},
            ],
            metrics={"total": 0, "correct": 0, "accuracy": None, "high_confidence_accuracy": None, "last_10_correct": 0, "last_10_total": 0},
            backtest=backtest,
            last_updated=None,
        )

    def test_future_provisional_and_locked_states(self):
        provisional = self.render([prediction_match()])
        self.assertIn("Provisional Prediction", provisional)
        self.assertNotIn("Actual result", provisional)
        self.assertNotIn('class="scoreline"', provisional)
        self.assertNotIn("CORRECT", provisional)

        locked = self.render([prediction_match(prediction_status="locked", locked_at="2026-08-23T12:00:00Z")])
        self.assertIn("Locked · Official Agent Pick", locked)
        self.assertNotIn("Actual result", locked)
        self.assertNotIn('class="scoreline"', locked)

    def test_completed_correct_uses_original_probabilities(self):
        html = self.render([prediction_match(status="completed", home_score=2, away_score=0, prediction_status="evaluated", locked_at="2026-08-22T12:00:00Z", actual_outcome="H", correct=True)])
        self.assertIn("Completed", html)
        self.assertNotIn("Provisional Prediction", html)
        self.assertIn('<em>2</em><span>—</span><em>0</em>', html)
        self.assertIn("FT", html)
        self.assertIn("Actual result", html)
        self.assertIn("Manchester City win", html)
        self.assertIn("Official pick", html)
        self.assertIn("53.4%", html)
        self.assertIn("33.0%", html)
        self.assertIn("13.6%", html)
        self.assertIn("✓ CORRECT", html)

    def test_completed_incorrect_locked_pick_is_official(self):
        html = self.render([prediction_match(status="completed", home_score=0, away_score=1, prediction_status="evaluated", locked_at="2026-08-22T12:00:00Z", actual_outcome="A", correct=False)])
        self.assertIn("Completed", html)
        self.assertIn("Bournemouth win", html)
        self.assertIn("Official pick", html)
        self.assertIn("× INCORRECT", html)
        self.assertNotIn("Provisional Prediction", html)

    def test_completed_draw_shows_draw_and_correct_draw_pick(self):
        html = self.render([prediction_match(status="completed", home_score=1, away_score=1, predicted_outcome="D", prediction_status="evaluated", locked_at="2026-08-22T12:00:00Z", actual_outcome="D", correct=True)])
        self.assertIn('<em>1</em><span>—</span><em>1</em>', html)
        self.assertRegex(html, r"Actual result</small><strong>Draw</strong>")
        self.assertIn("✓ CORRECT", html)

    def test_presentation_helper_does_not_mutate_prediction_or_evaluation_data(self):
        source = {
            "status": "completed", "home_team": "Manchester City FC", "away_team": "Bournemouth FC",
            "home_score": 2, "away_score": 0, "home_probability": 0.534, "draw_probability": 0.330,
            "away_probability": 0.136, "predicted_outcome": "H", "confidence": "High",
            "actual_outcome": "H", "correct": 1, "prediction_status": "evaluated", "locked_at": "2026-08-22T12:00:00Z",
        }
        before = deepcopy(source)
        prepared = _prepare_league_match(source)
        self.assertEqual(source, before)
        self.assertIsNot(prepared, source)
        for field in ("home_probability", "draw_probability", "away_probability", "predicted_outcome", "confidence", "actual_outcome", "correct", "prediction_status", "locked_at"):
            self.assertEqual(prepared[field], before[field])

    def test_standings_table_has_header_before_all_eight_cell_rows(self):
        html = self.render([])
        table = re.search(r'<table class="league-table">(.*?)</table>', html, re.DOTALL).group(1)
        thead_start, thead_end = table.index("<thead>"), table.index("</thead>")
        tbody_start, tbody_end = table.index("<tbody>"), table.index("</tbody>")

        self.assertLess(thead_start, tbody_start)
        self.assertNotIn("<td", table[:thead_start])
        self.assertEqual(table[thead_start:thead_end].count("<th "), 8)
        self.assertEqual(table.count("<thead>"), 1)

        body = table[tbody_start:tbody_end]
        rows = re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.count("<td") == 8 for row in rows))
        self.assertNotIn("<th", body)
        self.assertNotIn("Manchester City", table[:tbody_start])
        self.assertIn("Manchester City", body)

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

    def test_public_footer_has_no_github_link_and_keeps_fifa_archive(self):
        html = self.render([])
        self.assertNotIn("github.com", html.lower())
        self.assertNotIn("View on GitHub", html)
        self.assertIn('href="/fifa-2026"', html)
        self.assertIn("&copy; 2026 SportsIntelAI", html)

    def test_public_routes_render_without_github_link(self):
        client = TestClient(app)
        for path in ("/", "/league", "/fifa-2026"):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertNotIn("github.com", response.text.lower(), path)
        self.assertIn('href="/fifa-2026"', client.get("/").text)


if __name__ == "__main__":
    unittest.main()
