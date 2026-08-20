from __future__ import annotations


def expected_score(rating: float, opponent: float) -> float:
    return 1.0 / (1.0 + 10.0 ** ((opponent - rating) / 400.0))


def update_elo(home: float, away: float, home_goals: int, away_goals: int, k: float = 20.0, home_advantage: float = 80.0) -> tuple[float, float]:
    """Classic zero-sum Elo. Home advantage affects expectation, not stored strength."""
    actual = 1.0 if home_goals > away_goals else 0.0 if home_goals < away_goals else 0.5
    expected = expected_score(home + home_advantage, away)
    delta = k * (actual - expected)
    return home + delta, away - delta


def chronological_ratings(matches: list[dict], initial: float = 1500.0, k: float = 20.0, home_advantage: float = 80.0) -> tuple[dict[str, float], dict[str, tuple[float, float]]]:
    ratings: dict[str, float] = {}
    pre_match: dict[str, tuple[float, float]] = {}
    for match in sorted(matches, key=lambda m: (m.get("kickoff") or "", str(m["id"]))):
        home, away = match.get("home_team"), match.get("away_team")
        if not home or not away:
            continue
        hr, ar = ratings.get(home, initial), ratings.get(away, initial)
        pre_match[str(match["id"])] = (hr, ar)
        if match.get("status") == "completed" and match.get("home_score") is not None and match.get("away_score") is not None:
            ratings[home], ratings[away] = update_elo(hr, ar, int(match["home_score"]), int(match["away_score"]), k, home_advantage)
    return ratings, pre_match
