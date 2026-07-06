from __future__ import annotations

import unittest
from copy import deepcopy
from pathlib import Path

from src.bracket_engine import BracketEngine
from src.fixtures_loader import load_fixtures, load_strengths
from src.message_builder import change_summary, compare_snapshots
from src.prediction_engine import actual_winner


ROOT = Path(__file__).resolve().parent.parent


class DynamicBracketTests(unittest.TestCase):
    def setUp(self) -> None:
        strengths = load_strengths(ROOT / "data" / "team_strength.json")
        self.teams = [{"name": name, "base_strength": score} for name, score in strengths.items()]
        self.fixtures = load_fixtures(ROOT / "data" / "sample_fixtures.json")

    def build(self, fixtures: list[dict]) -> list[dict]:
        bracket, _, _ = BracketEngine(fixtures, self.teams).rebuild()
        return bracket

    def test_upset_propagation_and_change_explanation(self) -> None:
        baseline_fixtures = deepcopy(self.fixtures)
        match = next(item for item in baseline_fixtures if item["id"] == "R32-03")
        match.update(status="scheduled", home_score=None, away_score=None)
        baseline = self.build(baseline_fixtures)
        baseline_r16 = next(item for item in baseline if item["match_id"] == "R16-02")
        self.assertEqual(baseline_r16["home_team"], "France")

        actual_fixtures = deepcopy(self.fixtures)
        updated = self.build(actual_fixtures)
        updated_r16 = next(item for item in updated if item["match_id"] == "R16-02")
        self.assertEqual(updated_r16["home_team"], "Brazil")

        future_teams = {
            team
            for item in updated
            if item["status"] != "completed"
            for team in (item["home_team"], item["away_team"])
        }
        self.assertNotIn("France", future_teams)
        self.assertIn("France were knocked out by Brazil", change_summary(compare_snapshots(updated, baseline)))

    def test_penalty_result_is_resolved_as_actual_winner(self) -> None:
        match = next(item for item in self.fixtures if item["id"] == "R32-02")
        self.assertEqual(actual_winner(match), "Morocco")


if __name__ == "__main__":
    unittest.main()
