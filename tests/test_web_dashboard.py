from __future__ import annotations

import unittest

from src.web_app import _dashboard_data


class FakeDatabase:
    def latest_snapshots(self, limit: int = 2):
        bracket = [
            {"match_id": "qf", "match_number": 25, "stage": "Quarter-final", "kickoff": "2026-07-10T19:00:00Z", "status": "scheduled", "home_team": "Spain", "away_team": "Belgium", "winner": "Spain", "home_score": None, "away_score": None, "fixture_type": "predicted"},
            {"match_id": "sf", "match_number": 29, "stage": "Semi-final", "kickoff": "2026-07-14T19:00:00Z", "status": "scheduled", "home_team": "France", "away_team": "Spain", "winner": "Spain", "home_score": None, "away_score": None, "fixture_type": "predicted"},
            {"match_id": "final", "match_number": 31, "stage": "Final", "kickoff": "2026-07-19T19:00:00Z", "status": "scheduled", "home_team": "Spain", "away_team": "Argentina", "winner": "Spain", "home_score": None, "away_score": None, "fixture_type": "predicted"},
        ]
        return [{"id": 7, "created_at": "2026-07-06T00:00:00Z", "predicted_winner": "Spain", "change_summary": "Spain moved into the final.", "bracket": bracket}]

    def get_teams(self):
        return [{"name": team, "base_strength": strength, "eliminated": 0} for team, strength in {"Spain": 94, "Belgium": 84, "France": 95, "Argentina": 94}.items()]

    def rows(self, query, params=()):
        return [{"id": 9, "status": "success"}]


class DashboardShowcaseTests(unittest.TestCase):
    def test_summary_and_champion_path_are_exposed(self):
        data = _dashboard_data(FakeDatabase())
        self.assertEqual(data["champion"], "Spain")
        self.assertEqual(data["champion_path_ids"], {"qf", "sf", "final"})
        self.assertEqual(data["next_match"]["match_id"], "qf")
        self.assertEqual(data["biggest_change"], "Spain moved into the final.")


if __name__ == "__main__":
    unittest.main()
