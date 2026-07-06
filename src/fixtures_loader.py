from __future__ import annotations

import json
from pathlib import Path
from typing import Any


VALID_STAGES = {"Round of 32", "Round of 16", "Quarter-final", "Semi-final", "Final"}


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_fixtures(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    fixtures = payload.get("matches", payload) if isinstance(payload, dict) else payload
    if not isinstance(fixtures, list):
        raise ValueError("Fixture JSON must be a list or an object containing 'matches'.")
    normalized: list[dict[str, Any]] = []
    for item in fixtures:
        if not isinstance(item, dict) or not item.get("id") or not item.get("stage"):
            raise ValueError("Every fixture needs at least 'id' and 'stage'.")
        if item["stage"] not in VALID_STAGES:
            raise ValueError(f"Unsupported knockout stage: {item['stage']}")
        normalized.append(
            {
                "id": str(item["id"]),
                "stage": item["stage"],
                "match_number": int(item.get("match_number", 0)),
                "kickoff": item.get("kickoff"),
                "status": item.get("status", "scheduled").lower(),
                "home_team": item.get("home_team"),
                "away_team": item.get("away_team"),
                "home_score": item.get("home_score"),
                "away_score": item.get("away_score"),
                "home_penalties": item.get("home_penalties"),
                "away_penalties": item.get("away_penalties"),
                "winner_team": item.get("winner_team"),
                "home_source_match": item.get("home_source_match"),
                "away_source_match": item.get("away_source_match"),
            }
        )
    return normalized


def load_strengths(path: Path) -> dict[str, float]:
    payload = load_json(path)
    if not isinstance(payload, dict):
        raise ValueError("team_strength.json must be an object of team -> score.")
    return {str(team): float(score) for team, score in payload.items()}
