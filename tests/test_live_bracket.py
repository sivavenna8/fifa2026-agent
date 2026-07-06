from __future__ import annotations

import unittest

from src.api_client import link_fifa_2026_bracket
from src.bracket_engine import BracketEngine, predicted_champion


def match(match_id: str, stage: str, kickoff: str, home: str | None = None,
          away: str | None = None, status: str = "scheduled", winner: str | None = None) -> dict:
    home_score = 0 if status == "completed" else None
    away_score = 1 if status == "completed" else None
    return {
        "id": match_id, "stage": stage, "match_number": 0, "kickoff": kickoff,
        "status": status, "home_team": home, "away_team": away,
        "home_score": home_score, "away_score": away_score,
        "home_penalties": None, "away_penalties": None, "winner_team": winner,
        "home_source_match": None, "away_source_match": None,
    }


class LiveBracketResolutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.matches = [
            match("537376", "Round of 16", "2026-07-04T17:00:00Z", "Canada", "Morocco", "completed", "Morocco"),
            match("537375", "Round of 16", "2026-07-04T21:00:00Z", "Paraguay", "France", "completed", "France"),
            match("537377", "Round of 16", "2026-07-05T20:00:00Z", "Brazil", "Norway", "completed", "Norway"),
            match("537378", "Round of 16", "2026-07-06T00:00:00Z", "Mexico", "England"),
            match("537379", "Round of 16", "2026-07-06T19:00:00Z", "Portugal", "Spain"),
            match("537380", "Round of 16", "2026-07-07T00:00:00Z", "United States", "Belgium"),
            match("537381", "Round of 16", "2026-07-07T16:00:00Z", "Argentina", "Egypt"),
            match("537382", "Round of 16", "2026-07-07T20:00:00Z", "Switzerland", "Colombia"),
            match("537383", "Quarter-final", "2026-07-09T20:00:00Z", "France", "Morocco"),
            match("537384", "Quarter-final", "2026-07-10T19:00:00Z"),
            match("537385", "Quarter-final", "2026-07-11T21:00:00Z"),
            match("537386", "Quarter-final", "2026-07-12T01:00:00Z"),
            match("537387", "Semi-final", "2026-07-14T19:00:00Z"),
            match("537388", "Semi-final", "2026-07-15T19:00:00Z"),
            match("537390", "Final", "2026-07-19T19:00:00Z"),
        ]
        strengths = {
            "Canada": 78, "Morocco": 86, "Paraguay": 77, "France": 95,
            "Brazil": 93, "Norway": 84, "Mexico": 81, "England": 92,
            "Portugal": 91, "Spain": 94, "United States": 82, "Belgium": 84,
            "Argentina": 94, "Egypt": 75, "Switzerland": 83, "Colombia": 86,
        }
        self.teams = [{"name": team, "base_strength": score} for team, score in strengths.items()]

    def rebuild(self) -> list[dict]:
        linked = link_fifa_2026_bracket(self.matches)
        bracket, _, _ = BracketEngine(linked, self.teams).rebuild()
        return bracket

    def test_norway_and_predicted_england_advance_into_same_quarter_final(self) -> None:
        bracket = self.rebuild()
        quarter_final = next(item for item in bracket if item["match_id"] == "537385")
        self.assertEqual((quarter_final["home_team"], quarter_final["away_team"]), ("Norway", "England"))

    def test_other_tbd_quarter_finals_resolve_from_predictions(self) -> None:
        bracket = self.rebuild()
        qf_384 = next(item for item in bracket if item["match_id"] == "537384")
        qf_386 = next(item for item in bracket if item["match_id"] == "537386")
        self.assertEqual((qf_384["home_team"], qf_384["away_team"]), ("Spain", "Belgium"))
        self.assertEqual((qf_386["home_team"], qf_386["away_team"]), ("Argentina", "Colombia"))

    def test_final_has_predicted_winner(self) -> None:
        bracket = self.rebuild()
        final = next(item for item in bracket if item["stage"] == "Final")
        self.assertIsNotNone(final["home_team"])
        self.assertIsNotNone(final["away_team"])
        self.assertIsNotNone(final["winner"])
        self.assertEqual(predicted_champion(bracket), final["winner"])


if __name__ == "__main__":
    unittest.main()
