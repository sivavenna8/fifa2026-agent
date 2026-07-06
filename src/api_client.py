from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .fixtures_loader import VALID_STAGES

LOGGER = logging.getLogger(__name__)

# FIFA 2026 match order is not a straight adjacent pairing in chronological
# order. These indices map each next-round fixture to its two source fixtures.
# Explicit teams supplied by football-data.org remain authoritative; the links
# are used only while a future slot is still empty.
FIFA_2026_SOURCE_INDEXES = {
    "Round of 16": ((0, 3), (2, 5), (1, 4), (6, 7), (11, 10), (9, 8), (14, 13), (12, 15)),
    "Quarter-final": ((0, 1), (4, 5), (2, 3), (6, 7)),
    "Semi-final": ((0, 1), (2, 3)),
    "Final": ((0, 1),),
}
PREVIOUS_STAGE = {
    "Round of 16": "Round of 32",
    "Quarter-final": "Round of 16",
    "Semi-final": "Quarter-final",
    "Final": "Semi-final",
}


def link_fifa_2026_bracket(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach source-match links needed to resolve football-data.org TBD slots."""
    by_stage: dict[str, list[dict[str, Any]]] = {}
    for stage in VALID_STAGES:
        by_stage[stage] = sorted(
            (match for match in matches if match["stage"] == stage),
            key=lambda match: (match.get("kickoff") or "", match["id"]),
        )

    for stage, pairings in FIFA_2026_SOURCE_INDEXES.items():
        children = by_stage.get(stage, [])
        parents = by_stage.get(PREVIOUS_STAGE[stage], [])
        if len(children) != len(pairings) or not parents:
            continue
        for child, (home_index, away_index) in zip(children, pairings):
            if home_index >= len(parents) or away_index >= len(parents):
                continue
            child["home_source_match"] = child.get("home_source_match") or parents[home_index]["id"]
            child["away_source_match"] = child.get("away_source_match") or parents[away_index]["id"]
    return matches


class FixtureAPIError(RuntimeError):
    pass


class FixtureAPIClient:
    """JSON adapter for football-data.org v4 match responses."""

    def __init__(self, url: str, api_key: str | None = None, timeout: int = 15):
        self.url = url
        self.api_key = api_key
        self.timeout = timeout

    def fetch(self) -> list[dict[str, Any]]:
        headers = {"Accept": "application/json", "User-Agent": "fifa2026-bracket-agent/1.0"}
        if self.api_key:
            headers["X-Auth-Token"] = self.api_key
        try:
            with urlopen(Request(self.url, headers=headers), timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise FixtureAPIError(str(exc)) from exc
        raw_matches = payload.get("matches", payload) if isinstance(payload, dict) else payload
        if not isinstance(raw_matches, list):
            raise FixtureAPIError("API response did not contain a match list")
        adapted = [self._adapt(match) for match in raw_matches]
        knockout_matches = [match for match in adapted if match and match["stage"] in VALID_STAGES]
        stage_order = {"Round of 32": 0, "Round of 16": 1, "Quarter-final": 2, "Semi-final": 3, "Final": 4}
        knockout_matches.sort(key=lambda match: (stage_order[match["stage"]], match.get("kickoff") or "", match["id"]))
        for match_number, match in enumerate(knockout_matches, start=1):
            if not match.get("match_number"):
                match["match_number"] = match_number
        return link_fifa_2026_bracket(knockout_matches)

    @staticmethod
    def _team(value: Any) -> str | None:
        if isinstance(value, dict):
            return value.get("name") or value.get("shortName")
        return value

    def _adapt(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Accept native project JSON plus common football-data style fields."""
        stage_map = {
            "LAST_32": "Round of 32", "ROUND_OF_32": "Round of 32",
            "LAST_16": "Round of 16", "ROUND_OF_16": "Round of 16",
            "QUARTER_FINALS": "Quarter-final", "QUARTER_FINAL": "Quarter-final",
            "SEMI_FINALS": "Semi-final", "SEMI_FINAL": "Semi-final", "FINAL": "Final",
        }
        stage = raw.get("stage")
        stage = stage_map.get(str(stage).upper(), stage)
        score = raw.get("score") or {}
        full_time = score.get("fullTime") or score.get("full_time") or {}
        penalties = score.get("penalties") or {}
        status_map = {
            "FINISHED": "completed", "AWARDED": "completed",
            "IN_PLAY": "live", "LIVE": "live", "PAUSED": "live",
            "TIMED": "scheduled", "SCHEDULED": "scheduled",
            "POSTPONED": "postponed", "SUSPENDED": "postponed", "CANCELLED": "cancelled",
        }
        home_team = self._team(raw.get("home_team") or raw.get("homeTeam"))
        away_team = self._team(raw.get("away_team") or raw.get("awayTeam"))
        winner_code = str(score.get("winner") or "").upper()
        winner_team = raw.get("winner_team")
        if not winner_team and winner_code == "HOME_TEAM":
            winner_team = home_team
        elif not winner_team and winner_code == "AWAY_TEAM":
            winner_team = away_team
        return {
            "id": str(raw.get("id") or raw.get("match_id") or ""),
            "stage": stage,
            "match_number": int(raw.get("match_number") or raw.get("matchday") or 0),
            "kickoff": raw.get("kickoff") or raw.get("utcDate"),
            "status": status_map.get(str(raw.get("status", "scheduled")).upper(), str(raw.get("status", "scheduled")).lower()),
            "home_team": home_team,
            "away_team": away_team,
            "home_score": raw.get("home_score", full_time.get("home")),
            "away_score": raw.get("away_score", full_time.get("away")),
            "home_penalties": raw.get("home_penalties", penalties.get("home")),
            "away_penalties": raw.get("away_penalties", penalties.get("away")),
            "winner_team": winner_team,
            "home_source_match": raw.get("home_source_match"),
            "away_source_match": raw.get("away_source_match"),
        }
