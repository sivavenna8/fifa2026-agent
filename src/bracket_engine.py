from __future__ import annotations

import logging
from typing import Any

from .prediction_engine import PredictionEngine, actual_winner

LOGGER = logging.getLogger(__name__)
STAGE_ORDER = {"Round of 32": 1, "Round of 16": 2, "Quarter-final": 3, "Semi-final": 4, "Final": 5}
STAGE_LABEL = {"Round of 32": "R32", "Round of 16": "R16", "Quarter-final": "QF", "Semi-final": "SF", "Final": "Final"}


class BracketError(RuntimeError):
    pass


class BracketEngine:
    def __init__(self, matches: list[dict[str, Any]], teams: list[dict[str, Any]]):
        self.matches = sorted(matches, key=lambda m: (STAGE_ORDER.get(m["stage"], 99), m["match_number"], m["id"]))
        self.teams = teams

    def rebuild(self) -> tuple[list[dict[str, Any]], set[str], set[str]]:
        eliminated = self._actual_eliminations()
        predictor = PredictionEngine(self.teams, self.matches, eliminated)
        resolved_winners: dict[str, str] = {}
        bracket: list[dict[str, Any]] = []

        for match in self.matches:
            home = self._resolve_slot(match, "home", resolved_winners)
            away = self._resolve_slot(match, "away", resolved_winners)
            inferred = (
                (not match.get("home_team") and bool(home))
                or (not match.get("away_team") and bool(away))
            )
            if inferred and home and away:
                LOGGER.info(
                    "Inferred %s %s: %s vs %s from upstream winners",
                    STAGE_LABEL.get(match["stage"], match["stage"]),
                    match["id"], home, away,
                )
            completed = match["status"] == "completed"
            if completed:
                winner = actual_winner({**match, "home_team": home or match.get("home_team"), "away_team": away or match.get("away_team")})
                if not winner:
                    raise BracketError(f"Completed match {match['id']} has no resolvable winner")
                home = match.get("home_team") or home
                away = match.get("away_team") or away
                basis = "actual completed result (fixed)"
                home_model = away_model = None
            elif home and away:
                if home in eliminated or away in eliminated:
                    bad = home if home in eliminated else away
                    raise BracketError(f"Eliminated team {bad} reached future match {match['id']}")
                winner, home_model, away_model, basis = predictor.predict(home, away)
            else:
                winner = None
                home_model = away_model = None
                basis = "awaiting source fixture"
            if winner:
                resolved_winners[match["id"]] = winner
            confirmed = bool(match.get("home_team") and match.get("away_team"))
            bracket.append({
                "match_id": match["id"], "match_number": match["match_number"], "stage": match["stage"],
                "kickoff": match.get("kickoff"), "status": match["status"], "home_team": home,
                "away_team": away, "winner": winner, "home_score": match.get("home_score"),
                "away_score": match.get("away_score"), "home_penalties": match.get("home_penalties"),
                "away_penalties": match.get("away_penalties"), "home_model_score": home_model,
                "away_model_score": away_model, "fixture_type": "completed" if completed else ("confirmed" if confirmed else "predicted"),
                "basis": basis,
            })
            LOGGER.info("%s %s: %s vs %s -> %s (%s)", match["stage"], match["id"], home or "TBD", away or "TBD", winner or "TBD", bracket[-1]["fixture_type"])

        active = {team for item in bracket if item["status"] != "completed" for team in (item.get("home_team"), item.get("away_team")) if team}
        active -= eliminated
        return bracket, eliminated, active

    @staticmethod
    def _resolve_slot(match: dict[str, Any], side: str, winners: dict[str, str]) -> str | None:
        explicit_team = match.get(f"{side}_team")
        if explicit_team:
            return explicit_team
        source = match.get(f"{side}_source_match")
        if source:
            return winners.get(source)
        return None

    def _actual_eliminations(self) -> set[str]:
        eliminated: set[str] = set()
        for match in self.matches:
            if match["status"] != "completed":
                continue
            winner = actual_winner(match)
            if not winner:
                continue
            participants = {match.get("home_team"), match.get("away_team")}
            eliminated.update(team for team in participants if team and team != winner)
        return eliminated


def predicted_champion(bracket: list[dict[str, Any]]) -> str | None:
    final = next((item for item in bracket if item["stage"] == "Final"), None)
    return final.get("winner") if final else None
