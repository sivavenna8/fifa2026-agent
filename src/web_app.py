from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .database import Database
from .message_builder import build_message


ROOT = Path(__file__).resolve().parent.parent
STAGES = ("Round of 32", "Round of 16", "Quarter-final", "Semi-final", "Final")


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
    latest_results = [match for match in bracket if match["status"] == "completed"][-6:]
    runs = db.rows("SELECT * FROM agent_runs ORDER BY id DESC LIMIT 1")
    champion = next((match.get("winner") for match in bracket if match["stage"] == "Final"), None)
    return {
        "latest_run": runs[0] if runs else None,
        "latest_snapshot": latest,
        "latest_results": list(reversed(latest_results)),
        "eliminated": eliminated,
        "eliminated_set": set(eliminated),
        "strengths": strengths,
        "stages": [{"name": stage, "matches": grouped.get(stage, [])} for stage in STAGES],
        "champion": champion,
        "snapshots": snapshots,
        "telegram_preview": build_message(bracket, previous) if bracket else "Run python main.py daily to create the first briefing.",
        "counts": {
            "completed": sum(match["status"] == "completed" for match in bracket),
            "confirmed": sum(match["fixture_type"] == "confirmed" for match in bracket),
            "predicted": sum(match["fixture_type"] == "predicted" for match in bracket),
        },
    }


def create_app(database_path: Path) -> FastAPI:
    app = FastAPI(title="FIFA2026 Dynamic Bracket Agent", version="1.0.0")
    templates = Jinja2Templates(directory=ROOT / "templates")
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    db = Database(database_path)

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
