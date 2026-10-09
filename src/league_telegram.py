"""Read persisted league picks and publish a checkpointed match-day briefing."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from .database import Database, parse_utc

LOGGER = logging.getLogger(__name__)
LONDON = ZoneInfo("Europe/London")
MAX_UNITS = 3900
FOOTER = "━━━━━━━━━━━━━━\n🤖 SportsIntelAI\nML-powered football intelligence"


def units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def clean(value: object) -> str:
    return " ".join(str(value).split())


def valid_prediction(match: dict) -> bool:
    probabilities = [match.get(f"{side}_probability") for side in ("home", "draw", "away")]
    return (
        all(isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1 for p in probabilities)
        and abs(sum(probabilities) - 1) < .01
        and match.get("predicted_outcome") in {"H", "D", "A"}
        and match.get("prediction_status") in {"locked", "evaluated", "provisional"}
        and match.get("confidence") in {"High", "Medium", "Low"}
    )


def build_messages(matches: list[dict], league_name: str, day) -> list[str]:
    header = f"⚽ SPORTSINTELAI — TODAY'S PREDICTIONS\n\n{league_name} · {day.strftime('%d %B %Y')}"
    continuation = f"⚽ SportsIntelAI · {league_name} · {day.isoformat()} (continued)"
    messages = []
    current = header
    for match in matches:
        block = f"{clean(match['home_team'])} vs {clean(match['away_team'])}\n"
        if valid_prediction(match):
            block += " · ".join(f"{side.title()} {match[f'{side}_probability']:.1%}" for side in ("home", "draw", "away")) + "\n"
            label = "Provisional Pick" if match["prediction_status"] == "provisional" else "Official Pick"
            pick = {"H": match["home_team"], "D": "Draw", "A": match["away_team"]}[match["predicted_outcome"]]
            block += f"🎯 {label}: {clean(pick)}\nConfidence: {match['confidence']}\n"
        else:
            block += "Prediction unavailable\n"
            LOGGER.warning("[telegram-agent] Prediction unavailable for fixture %s", match['id'])
        block += f"Kickoff: {parse_utc(match['kickoff']).astimezone(LONDON):%H:%M} UK"
        if units(continuation + "\n\n" + block + "\n\n" + FOOTER) > MAX_UNITS:
            raise ValueError("A fixture is too large for a Telegram message")
        if units(current + "\n\n" + block + "\n\n" + FOOTER) > MAX_UNITS:
            messages.append(current + "\n\n" + FOOTER)
            current = continuation
        current += "\n\n" + block
    if matches:
        messages.append(current + "\n\n" + FOOTER)
    return messages


def _lock(connection, db: Database, key: str) -> None:
    # Transaction-scoped locks serialize manual runs and workflow reruns too.
    if db.backend == "postgres":
        connection.execute("SELECT pg_advisory_xact_lock(hashtext(?))", (key,))
    else:
        connection.execute("BEGIN IMMEDIATE")


def run_match_day(db: Database, code: str = "PL", *, dry_run: bool = False,
                  scheduled: bool = False, now: datetime | None = None,
                  publication: str = "MORNING", target_date=None, recover: bool = False) -> str:
    """Compatibility CLI entrypoint; V2 never refreshes or creates schema."""
    from .league_publications import publish
    result = publish(db, code, publication, dry_run=dry_run, now=now,
                     target_date=target_date, recover=recover)
    return result['status']
