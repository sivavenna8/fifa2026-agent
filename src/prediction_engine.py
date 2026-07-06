from __future__ import annotations

from collections import defaultdict
from typing import Any


class PredictionEngine:
    """Transparent deterministic scoring; all component weights are inspectable."""

    def __init__(self, teams: list[dict[str, Any]], matches: list[dict[str, Any]], eliminated: set[str]):
        self.strengths = {team["name"]: float(team["base_strength"]) for team in teams}
        self.eliminated = eliminated
        self.stats: dict[str, dict[str, float]] = defaultdict(lambda: {
            "played": 0, "wins": 0, "goals_for": 0, "goals_against": 0,
            "knockout_wins": 0, "opponent_strength": 0,
        })
        for match in matches:
            if match["status"] != "completed" or not match.get("home_team") or not match.get("away_team"):
                continue
            home, away = match["home_team"], match["away_team"]
            hs, ass = match.get("home_score") or 0, match.get("away_score") or 0
            for team, gf, ga, opponent in ((home, hs, ass, away), (away, ass, hs, home)):
                self.stats[team]["played"] += 1
                self.stats[team]["goals_for"] += gf
                self.stats[team]["goals_against"] += ga
                self.stats[team]["opponent_strength"] += self.strengths.get(opponent, 50)
            winner = actual_winner(match)
            if winner:
                self.stats[winner]["wins"] += 1
                self.stats[winner]["knockout_wins"] += 1

    def score(self, team: str, opponent: str) -> tuple[float, dict[str, float]]:
        if team in self.eliminated:
            return -9999.0, {"elimination": -9999.0}
        stat = self.stats[team]
        played = max(1.0, stat["played"])
        components = {
            "base_strength": self.strengths.get(team, 50.0) * 0.70,
            "recent_performance": (stat["wins"] / played) * 10.0,
            "goal_difference": ((stat["goals_for"] - stat["goals_against"]) / played) * 2.0,
            "knockout_wins": stat["knockout_wins"] * 1.5,
            "opponent_quality": (stat["opponent_strength"] / played) * 0.04 if stat["played"] else 0.0,
            "matchup": (self.strengths.get(team, 50.0) - self.strengths.get(opponent, 50.0)) * 0.08,
        }
        return round(sum(components.values()), 3), components

    def predict(self, home: str, away: str) -> tuple[str, float, float, str]:
        home_score, home_parts = self.score(home, away)
        away_score, away_parts = self.score(away, home)
        if home_score == away_score:
            winner = max((home, away), key=lambda team: (self.strengths.get(team, 50), team))
            handling = "model draw resolved by base strength"
        else:
            winner = home if home_score > away_score else away
            handling = "higher transparent model score"
        basis = (
            f"{handling}; {home}={home_score:.2f} vs {away}={away_score:.2f}. "
            f"Factors: strength, form, goals, knockout wins, opponent quality, matchup."
        )
        return winner, home_score, away_score, basis


def actual_winner(match: dict[str, Any]) -> str | None:
    if match.get("winner_team"):
        return match["winner_team"]
    home, away = match.get("home_team"), match.get("away_team")
    hs, ass = match.get("home_score"), match.get("away_score")
    if not home or not away or hs is None or ass is None:
        return None
    if hs > ass:
        return home
    if ass > hs:
        return away
    hp, ap = match.get("home_penalties"), match.get("away_penalties")
    if hp is not None and ap is not None and hp != ap:
        return home if hp > ap else away
    return None
