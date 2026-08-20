from __future__ import annotations

from datetime import datetime
from typing import Any

from .elo import chronological_ratings

FEATURE_NAMES = ["elo_diff", "home_ppg_5", "away_ppg_5", "form_points_diff", "goals_scored_diff", "goals_conceded_diff", "goal_difference_diff", "home_venue_ppg", "away_venue_ppg", "rest_days_diff", "matchday"]


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def build_feature_rows(matches: list[dict[str, Any]], initial_elo: float = 1500.0, home_advantage: float = 80.0) -> list[dict[str, Any]]:
    """Walk forward in time. A row can only inspect matches completed earlier."""
    ordered = sorted(matches, key=lambda m: (m.get("kickoff") or "", str(m["id"])))
    _, pre = chronological_ratings(ordered, initial_elo, home_advantage=home_advantage)
    history: list[dict[str, Any]] = []
    rows = []
    for match in ordered:
        home, away = match.get("home_team"), match.get("away_team")
        if not home or not away:
            continue
        prior = [m for m in history if (m.get("kickoff") or "") < (match.get("kickoff") or "")]
        def stats(team: str, limit: int = 5, venue: str | None = None) -> tuple[float, float, float, float]:
            games = [m for m in prior if (m["home_team"] == team or m["away_team"] == team) and (venue is None or (venue == "home" and m["home_team"] == team) or (venue == "away" and m["away_team"] == team))][-limit:]
            if not games: return 1.0, 1.0, 1.0, 0.0
            pts = gf = ga = 0
            for g in games:
                is_home = g["home_team"] == team; a, b = (g["home_score"], g["away_score"]) if is_home else (g["away_score"], g["home_score"])
                gf += a; ga += b; pts += 3 if a > b else 1 if a == b else 0
            return pts / len(games), gf / len(games), ga / len(games), (gf-ga) / len(games)
        hs, aws = stats(home), stats(away); hv, av = stats(home, 10, "home"), stats(away, 10, "away")
        def rest(team: str) -> float:
            dates = [_dt(m["kickoff"]) for m in prior if team in (m["home_team"], m["away_team"])]
            current = _dt(match.get("kickoff")); return min(14.0, float((current-max(dates)).days)) if dates and current else 7.0
        hr, ar = pre[str(match["id"])]
        values = [hr + home_advantage-ar, hs[0], aws[0], hs[0]-aws[0], hs[1]-aws[1], hs[2]-aws[2], hs[3]-aws[3], hv[0], av[0], rest(home)-rest(away), float(match.get("matchday") or 0)]
        result = None if match.get("status") != "completed" else ("H" if match["home_score"] > match["away_score"] else "A" if match["home_score"] < match["away_score"] else "D")
        rows.append({"match_id": str(match["id"]), "kickoff": match.get("kickoff"), "season": match.get("season"), "features": values, "result": result})
        if result: history.append(match)
    return rows
