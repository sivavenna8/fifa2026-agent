from __future__ import annotations

import json
import logging
import time
import os
from pathlib import Path
from typing import Any

from .config import DEFAULT_FOOTBALL_DATA_BASE_URL, get_settings
from .database import Database, utc_now
from .elo import chronological_ratings
from .league_api import LeagueAPIClient
from .league_config import get_league
from .league_features import FEATURE_NAMES, build_feature_rows
from .league_model import LeagueModel

LOGGER = logging.getLogger(__name__)


def production_model_path(code: str) -> Path:
    return Path(os.getenv("MODEL_PATH",f"models/{code.lower()}_model.pkl"))


def fetch_league(db: Database, code: str, season: int | None = None) -> dict[str, Any]:
    league=get_league(code); settings=get_settings()
    if not settings.api_key: raise ValueError("FOOTBALL_DATA_API_KEY is required for league fetching")
    client=LeagueAPIClient(DEFAULT_FOOTBALL_DATA_BASE_URL if not settings.api_url else settings.api_url.split("/competitions/")[0],settings.api_key,settings.request_timeout)
    matches=client.matches(league.code,season); inserted,updated=db.upsert_league_matches(league,matches)
    standings=[]
    try:
        if season is not None:
            raise ValueError("Historical standings are not required")
        standings=client.standings(league.code); now=utc_now()
        with db.connect() as c:
            for r in standings: c.execute("INSERT INTO league_standings VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(league_code,team_id) DO UPDATE SET team=excluded.team,position=excluded.position,played=excluded.played,won=excluded.won,drawn=excluded.drawn,lost=excluded.lost,goal_difference=excluded.goal_difference,points=excluded.points,updated_at=excluded.updated_at",(league.code,r["team_id"],r["team"],r["position"],r["played"],r["won"],r["drawn"],r["lost"],r["goal_difference"],r["points"],now))
    except Exception:
        standings=[]  # matches remain useful if the account cannot access standings
    return {"season":season if season is not None else "current","matches":len(matches),"inserted":inserted,"updated":updated,"standings":len(standings)}


def fetch_league_history(db: Database, code: str, from_season: int | None=None, to_season: int | None=None) -> dict[str, Any]:
    start=2023 if from_season is None else from_season; end=2025 if to_season is None else to_season
    if start>end: raise ValueError("--from-season must not exceed --to-season")
    reports=[]
    for season in range(start,end+1):
        reports.append(fetch_league(db,code,season)); time.sleep(1.2)
    reports.append(fetch_league(db,code,None))
    matches=db.league_matches(code); completed=[m for m in matches if m["status"]=="completed"]
    outcome=lambda m: "H" if m["home_score"]>m["away_score"] else "A" if m["home_score"]<m["away_score"] else "D"
    counts={key:sum(outcome(m)==key for m in completed) for key in "HDA"}
    return {"fetches":reports,"stored":len(matches),"completed":len(completed),"scheduled":sum(m["status"]=="scheduled" for m in matches),"seasons":sorted({m["season"] for m in matches}),"earliest":min(m["kickoff"] for m in matches),"latest":max(m["kickoff"] for m in matches),"home_wins":counts["H"],"draws":counts["D"],"away_wins":counts["A"]}


def train_league(db: Database, code: str, model_dir: Path = Path("models")) -> dict[str,float]:
    league=get_league(code); rows=build_feature_rows(db.league_matches(code),league.initial_elo,league.home_advantage)
    model=LeagueModel(); metrics=model.train(rows); metrics["feature_names"]=FEATURE_NAMES; metrics["skipped_rows"]={"not_completed":sum(not r.get("result") for r in rows)}; model.metadata=metrics; model.save(model_dir/f"{code.lower()}_model.pkl")
    with db.connect() as c: c.execute("INSERT INTO model_metrics(league_code,model_name,created_at,split_type,metrics_json) VALUES(?,?,?,?,?)",(code,metrics["selected_model"],utc_now(),"chronological-70-15-15",json.dumps(metrics)))
    return metrics


def predict_league(db: Database, code: str, model_dir: Path | None=None, lock_window_hours: int=48) -> dict[str,int]:
    league=get_league(code); artifact=(model_dir/f"{code.lower()}_model.pkl") if model_dir else production_model_path(code)
    if not artifact.exists(): raise ValueError(f"No trained model at {artifact}. Run: python main.py league-train {code}")
    model=LeagueModel.load(artifact)
    all_matches=db.league_matches(code); rows=build_feature_rows(all_matches,league.initial_elo,league.home_advantage); matches={m["id"]:m for m in all_matches}; counts={"provisional":0,"locked":0}
    historical_teams={t for m in all_matches if m["status"]=="completed" for t in (m["home_team"],m["away_team"])}
    fallback_teams=sorted({t for m in all_matches if m["status"]=="scheduled" for t in (m["home_team"],m["away_team"])}-historical_teams)
    if fallback_teams: LOGGER.info("Neutral promoted-team fallback (1500 Elo / neutral form): %s",", ".join(fallback_teams))
    for row in rows:
        match=matches[row["match_id"]]
        if match["status"]=="scheduled":
            status=db.save_league_prediction(row["match_id"],code,model.predict(row["features"]),row["features"],model_name=model.model_name,model_version="v2.1",lock_window_hours=lock_window_hours)
            if status: counts[status]+=1
    ratings,_=chronological_ratings(list(matches.values()),league.initial_elo,league.elo_k,league.home_advantage)
    with db.connect() as c:
        for team,rating in ratings.items(): c.execute("INSERT INTO team_ratings VALUES(?,?,?,?) ON CONFLICT(league_code,team) DO UPDATE SET rating=excluded.rating,as_of=excluded.as_of",(code,team,rating,utc_now()))
    return counts


def daily_league(db: Database, code: str) -> dict[str,Any]:
    fetched=fetch_league(db,code); evaluated=db.evaluate_league_predictions(code)
    artifact=production_model_path(code)
    if not artifact.exists(): raise ValueError(f"No production model at {artifact}; train explicitly before running league-daily")
    predicted=predict_league(db,code)
    return {**fetched,"evaluated":evaluated,"predictions_refreshed":predicted,"performance":db.league_metrics(code)}


def bootstrap_league(db: Database, code: str="PL") -> None:
    """Initialize a brand-new persistent production volume exactly once."""
    if not db.league_matches(code):
        LOGGER.info("No %s data found; bootstrapping persistent league history",code)
        fetch_league_history(db,code)
    artifact=production_model_path(code)
    if not artifact.exists(): raise ValueError(f"Production model artifact is missing: {artifact}")
    metrics=db.rows("SELECT id FROM model_metrics WHERE league_code=? LIMIT 1",(code,))
    if not metrics:
        model=LeagueModel.load(artifact)
        with db.connect() as c:
            c.execute("INSERT INTO model_metrics(league_code,model_name,created_at,split_type,metrics_json) VALUES(?,?,?,?,?)",(code,model.model_name,utc_now(),"chronological-70-15-15",json.dumps(model.metadata)))
    predict_league(db,code)
