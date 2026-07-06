from __future__ import annotations

import logging
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .database import Database
from .message_builder import build_message


ROOT = Path(__file__).resolve().parent.parent
STAGES = ("Round of 32", "Round of 16", "Quarter-final", "Semi-final", "Final")
LOGGER = logging.getLogger(__name__)


def _bootstrap_dashboard(db: Database) -> None:
    """Populate an empty deployment and guarantee the first dashboard snapshot."""
    from main import fetch_data, rebuild

    if not db.get_matches():
        LOGGER.info("Dashboard database is empty; fetching initial fixture data")
        fetch_data(db)
    if not db.latest_snapshots(1):
        LOGGER.info("Dashboard has no bracket snapshot; building the initial bracket")
        rebuild(db, "web-startup", print_table=False)


def _dashboard_data(db: Database) -> dict[str, Any]:
    snapshots = db.latest_snapshots(20)
    latest = snapshots[0] if snapshots else None
    previous = snapshots[1]["bracket"] if len(snapshots) > 1 else None
    bracket = latest["bracket"] if latest else []
    teams = db.get_teams()
    strengths = {team["name"]: round(float(team["base_strength"])) for team in teams}
    eliminated = sorted(team["name"] for team in teams if team["eliminated"])
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for match in bracket:
        grouped[match["stage"]].append(match)
    completed_matches = [match for match in bracket if match["status"] == "completed"]
    latest_results = completed_matches[-6:]
    latest_completed = completed_matches[-1] if completed_matches else None
    upcoming_matches = [match for match in bracket if match["status"] != "completed" and match.get("home_team") and match.get("away_team")]
    next_match = min(upcoming_matches, key=lambda match: (match.get("kickoff") or "9999", match.get("match_number", 999))) if upcoming_matches else None
    runs = db.rows("SELECT * FROM agent_runs ORDER BY id DESC LIMIT 1")
    champion = next((match.get("winner") for match in bracket if match["stage"] == "Final"), None)
    champion_path_ids = {
        match["match_id"] for match in bracket
        if champion and match["stage"] in {"Quarter-final", "Semi-final", "Final"} and match.get("winner") == champion
    }
    biggest_change = latest.get("change_summary") if latest else None
    return {
        "latest_run": runs[0] if runs else None,
        "latest_snapshot": latest,
        "latest_results": list(reversed(latest_results)),
        "latest_completed": latest_completed,
        "next_match": next_match,
        "biggest_change": biggest_change or "No bracket-path change in the latest run.",
        "eliminated": eliminated,
        "eliminated_set": set(eliminated),
        "strengths": strengths,
        "stages": [{"name": stage, "matches": grouped.get(stage, [])} for stage in STAGES],
        "champion": champion,
        "champion_path_ids": champion_path_ids,
        "snapshots": snapshots,
        "telegram_preview": build_message(bracket, previous) if bracket else "Run python main.py daily to create the first briefing.",
        "counts": {
            "completed": sum(match["status"] == "completed" for match in bracket),
            "confirmed": sum(match["fixture_type"] == "confirmed" for match in bracket),
            "predicted": sum(match["fixture_type"] == "predicted" for match in bracket),
        },
    }


def create_app(database_path: Path) -> FastAPI:
    db = Database(database_path)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        _bootstrap_dashboard(db)
        yield

    app = FastAPI(title="FIFA2026 Dynamic Bracket Agent", version="1.0.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=ROOT / "templates")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"dashboard": _dashboard_data(db)},
        )

    @app.get("/api/dashboard")
    def dashboard_api() -> dict[str, Any]:
        data = _dashboard_data(db)
        data.pop("eliminated_set", None)
        return data

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


# Production ASGI entrypoint used by Render and other Uvicorn deployments.
app = create_app(get_settings().database_path)
