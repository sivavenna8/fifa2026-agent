from __future__ import annotations

import logging
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from datetime import date as date_type, timedelta
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import get_settings
from .database import Database
from .message_builder import build_message
from .league_config import LEAGUES, get_league


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
    settings=get_settings()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            _bootstrap_dashboard(db)
        except Exception:
            LOGGER.exception("FIFA archive bootstrap failed; league dashboard will remain available")
        if settings.bootstrap_league_data:
            from .league_service import bootstrap_league
            bootstrap_league(db,"PL")
        if settings.enable_league_scheduler:
            from .scheduler import start_league_scheduler
            start_league_scheduler(database_path,settings.league_schedule_hours,"PL")
        yield

    app = FastAPI(title="SportsIntelAI", version="2.1.0", lifespan=lifespan)
    templates = Jinja2Templates(directory=ROOT / "templates")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

    @app.get("/fifa-2026", response_class=HTMLResponse)
    def fifa_archive(request: Request) -> HTMLResponse:
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

    @app.get("/", response_class=HTMLResponse)
    @app.get("/league", response_class=HTMLResponse)
    def league_dashboard(request: Request, league: str="PL", date: str | None=None) -> HTMLResponse:
        selected=date or date_type.today().isoformat(); selected_day=date_type.fromisoformat(selected); config=get_league(league)
        matches=db.league_matches(config.code,selected); london=ZoneInfo("Europe/London")
        for match in matches:
            if match.get("kickoff"):
                kickoff=__import__("datetime").datetime.fromisoformat(match["kickoff"].replace("Z","+00:00")).astimezone(london)
                match["kickoff_uk"]=kickoff.strftime("%d %b · %H:%M UK").upper()
        standings=db.rows("SELECT * FROM league_standings WHERE league_code=? ORDER BY position",(config.code,))
        backtests=db.rows("SELECT * FROM model_metrics WHERE league_code=? ORDER BY id DESC LIMIT 1",(config.code,))
        if backtests:
            backtests[0]["metrics"]=json.loads(backtests[0]["metrics_json"])
        next_rows=db.rows("SELECT substr(kickoff,1,10) match_date FROM league_matches WHERE league_code=? AND substr(kickoff,1,10)>? GROUP BY match_date ORDER BY match_date LIMIT 1",(config.code,selected))
        updated=db.rows("SELECT MAX(updated_at) updated_at FROM league_matches WHERE league_code=?",(config.code,))
        return templates.TemplateResponse(request=request,name="league_dashboard.html",context={"league":config,"leagues":[item for item in LEAGUES.values() if item.enabled],"selected_date":selected,"display_date":selected_day.strftime("%d %b %Y").upper(),"previous_date":(selected_day-timedelta(days=1)).isoformat(),"next_date":(selected_day+timedelta(days=1)).isoformat(),"today":date_type.today().isoformat(),"yesterday":(date_type.today()-timedelta(days=1)).isoformat(),"tomorrow":(date_type.today()+timedelta(days=1)).isoformat(),"next_matchday":next_rows[0]["match_date"] if next_rows else None,"matches":matches,"standings":standings,"metrics":db.league_metrics(config.code),"backtest":backtests[0] if backtests else None,"last_updated":updated[0]["updated_at"] if updated else None})

    @app.get("/api/leagues/{league}/matches")
    def league_matches(league: str, date: str | None=None): return {"league":league.upper(),"date":date,"matches":db.league_matches(league.upper(),date)}

    @app.get("/api/leagues/{league}/standings")
    def league_standings(league: str): return {"standings":db.rows("SELECT * FROM league_standings WHERE league_code=? ORDER BY position",(league.upper(),))}

    @app.get("/api/leagues/{league}/performance")
    def league_performance(league: str): return db.league_metrics(league.upper())

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok","service":"sportsintelai"}

    return app


# Production ASGI entrypoint used by Render and other Uvicorn deployments.
app = create_app(get_settings().database_path)
