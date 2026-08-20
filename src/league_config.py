from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LeagueConfig:
    code: str
    name: str
    country: str
    enabled: bool = True
    initial_elo: float = 1500.0
    home_advantage: float = 80.0
    elo_k: float = 20.0


LEAGUES = {"PL": LeagueConfig("PL", "Premier League", "England")}


def get_league(code: str) -> LeagueConfig:
    key = code.upper()
    if key not in LEAGUES or not LEAGUES[key].enabled:
        raise ValueError(f"League {code!r} is not enabled. Enabled: {', '.join(LEAGUES)}")
    return LEAGUES[key]
