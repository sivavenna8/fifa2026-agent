from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .api_client import FixtureAPIError


class LeagueAPIClient:
    """Small football-data.org v4 client; secrets never leave the server."""

    def __init__(self, base_url: str, api_key: str, timeout: int = 15):
        self.base_url, self.api_key, self.timeout = base_url.rstrip("/"), api_key, timeout

    def _get(self, path: str) -> dict[str, Any]:
        request = Request(self.base_url + path, headers={"X-Auth-Token": self.api_key, "Accept": "application/json", "User-Agent": "sportsintelai/2.0"})
        for attempt in range(3):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                if exc.code == 429 and attempt < 2:
                    time.sleep(6 * (attempt + 1))
                    continue
                raise FixtureAPIError(f"football-data.org request failed: {exc}") from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise FixtureAPIError(f"football-data.org request failed: {exc}") from exc
        raise FixtureAPIError("football-data.org request failed after retries")

    def matches(self, code: str, season: int | None = None) -> list[dict[str, Any]]:
        suffix = f"?season={season}" if season is not None else ""
        payload = self._get(f"/competitions/{code}/matches{suffix}")
        return [self._match(code, item) for item in payload.get("matches", [])]

    def standings(self, code: str) -> list[dict[str, Any]]:
        payload = self._get(f"/competitions/{code}/standings")
        total = next((s for s in payload.get("standings", []) if s.get("type") == "TOTAL"), {})
        return [{"position": r.get("position"), "team_id": str((r.get("team") or {}).get("id", "")), "team": (r.get("team") or {}).get("name"), "played": r.get("playedGames", 0), "won": r.get("won", 0), "drawn": r.get("draw", 0), "lost": r.get("lost", 0), "goal_difference": r.get("goalDifference", 0), "points": r.get("points", 0)} for r in total.get("table", [])]

    @staticmethod
    def _match(code: str, raw: dict[str, Any]) -> dict[str, Any]:
        score = (raw.get("score") or {}).get("fullTime") or {}
        status = str(raw.get("status", "SCHEDULED")).upper()
        return {"id": str(raw.get("id")), "league_code": code, "season": str(((raw.get("season") or {}).get("startDate") or "")[:4]), "matchday": raw.get("matchday"), "kickoff": raw.get("utcDate"), "status": {"FINISHED": "completed", "AWARDED": "completed", "IN_PLAY": "live", "PAUSED": "live", "POSTPONED": "postponed", "CANCELLED": "cancelled"}.get(status, "scheduled"), "home_team_id": str((raw.get("homeTeam") or {}).get("id", "")), "home_team": (raw.get("homeTeam") or {}).get("name"), "away_team_id": str((raw.get("awayTeam") or {}).get("id", "")), "away_team": (raw.get("awayTeam") or {}).get("name"), "home_score": score.get("home"), "away_score": score.get("away"), "winner": (raw.get("score") or {}).get("winner")}
