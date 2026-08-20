from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .database import Database
from .league_service import daily_league

LOGGER=logging.getLogger(__name__)


def _next_run(hours: tuple[int,...]) -> datetime:
    now=datetime.now(timezone.utc)
    candidates=[now.replace(hour=hour,minute=0,second=0,microsecond=0) for hour in hours]
    future=[candidate for candidate in candidates if candidate>now]
    return min(future) if future else min(candidates)+timedelta(days=1)


def start_league_scheduler(database_path: Path, hours: tuple[int,...]=(8,20), league: str="PL") -> threading.Thread:
    """Run the stateful daily agent inside the single disk-backed web service."""
    def worker() -> None:
        while True:
            target=_next_run(hours); delay=max(1.0,(target-datetime.now(timezone.utc)).total_seconds())
            LOGGER.info("Next %s daily agent run scheduled for %s",league,target.isoformat())
            if threading.Event().wait(delay): return
            try:
                result=daily_league(Database(database_path),league)
                LOGGER.info("Scheduled %s agent completed: %s",league,result)
            except Exception:
                LOGGER.exception("Scheduled %s agent failed",league)
    thread=threading.Thread(target=worker,name=f"sportsintel-{league.lower()}-scheduler",daemon=True)
    thread.start(); return thread
