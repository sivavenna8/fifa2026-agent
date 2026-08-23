from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    name TEXT PRIMARY KEY,
    base_strength REAL NOT NULL DEFAULT 50,
    qualified INTEGER NOT NULL DEFAULT 1,
    eliminated INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    match_number INTEGER NOT NULL DEFAULT 0,
    kickoff TEXT,
    status TEXT NOT NULL,
    home_team TEXT,
    away_team TEXT,
    home_score INTEGER,
    away_score INTEGER,
    home_penalties INTEGER,
    away_penalties INTEGER,
    winner_team TEXT,
    home_source_match TEXT,
    away_source_match TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    command TEXT NOT NULL,
    status TEXT NOT NULL,
    details TEXT
);
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    match_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    home_team TEXT,
    away_team TEXT,
    predicted_winner TEXT,
    home_score REAL,
    away_score REAL,
    basis TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES agent_runs(id),
    UNIQUE(run_id, match_id)
);
CREATE TABLE IF NOT EXISTS bracket_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    predicted_winner TEXT,
    bracket_json TEXT NOT NULL,
    change_summary TEXT,
    FOREIGN KEY(run_id) REFERENCES agent_runs(id)
);
CREATE TABLE IF NOT EXISTS leagues (code TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS league_matches (id TEXT PRIMARY KEY, league_code TEXT NOT NULL, season TEXT, matchday INTEGER, kickoff TEXT, status TEXT NOT NULL, home_team_id TEXT, home_team TEXT NOT NULL, away_team_id TEXT, away_team TEXT NOT NULL, home_score INTEGER, away_score INTEGER, winner TEXT, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_league_matches_date ON league_matches(league_code, kickoff);
CREATE TABLE IF NOT EXISTS league_standings (league_code TEXT NOT NULL, team_id TEXT NOT NULL, team TEXT NOT NULL, position INTEGER, played INTEGER, won INTEGER, drawn INTEGER, lost INTEGER, goal_difference INTEGER, points INTEGER, updated_at TEXT NOT NULL, PRIMARY KEY(league_code, team_id));
CREATE TABLE IF NOT EXISTS league_predictions (id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT NOT NULL UNIQUE, league_code TEXT NOT NULL, model_name TEXT NOT NULL, model_version TEXT NOT NULL, created_at TEXT NOT NULL, locked_at TEXT NOT NULL DEFAULT '', home_probability REAL NOT NULL, draw_probability REAL NOT NULL, away_probability REAL NOT NULL, predicted_outcome TEXT NOT NULL, confidence TEXT NOT NULL, feature_json TEXT NOT NULL, actual_outcome TEXT, correct INTEGER, evaluated_at TEXT, prediction_status TEXT NOT NULL DEFAULT 'provisional', generated_at TEXT);
CREATE TABLE IF NOT EXISTS team_ratings (league_code TEXT NOT NULL, team TEXT NOT NULL, rating REAL NOT NULL, as_of TEXT NOT NULL, PRIMARY KEY(league_code, team));
CREATE TABLE IF NOT EXISTS model_metrics (id INTEGER PRIMARY KEY AUTOINCREMENT, league_code TEXT NOT NULL, model_name TEXT NOT NULL, created_at TEXT NOT NULL, split_type TEXT NOT NULL, metrics_json TEXT NOT NULL);
"""

POSTGRES_SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (name TEXT PRIMARY KEY, base_strength DOUBLE PRECISION NOT NULL DEFAULT 50, qualified INTEGER NOT NULL DEFAULT 1, eliminated INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS matches (id TEXT PRIMARY KEY, stage TEXT NOT NULL, match_number INTEGER NOT NULL DEFAULT 0, kickoff TEXT, status TEXT NOT NULL, home_team TEXT, away_team TEXT, home_score INTEGER, away_score INTEGER, home_penalties INTEGER, away_penalties INTEGER, winner_team TEXT, home_source_match TEXT, away_source_match TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_runs (id BIGSERIAL PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT, command TEXT NOT NULL, status TEXT NOT NULL, details TEXT);
CREATE TABLE IF NOT EXISTS predictions (id BIGSERIAL PRIMARY KEY, run_id BIGINT NOT NULL REFERENCES agent_runs(id), match_id TEXT NOT NULL, stage TEXT NOT NULL, home_team TEXT, away_team TEXT, predicted_winner TEXT, home_score DOUBLE PRECISION, away_score DOUBLE PRECISION, basis TEXT NOT NULL, UNIQUE(run_id, match_id));
CREATE TABLE IF NOT EXISTS bracket_snapshots (id BIGSERIAL PRIMARY KEY, run_id BIGINT NOT NULL UNIQUE REFERENCES agent_runs(id), created_at TEXT NOT NULL, predicted_winner TEXT, bracket_json TEXT NOT NULL, change_summary TEXT);
CREATE TABLE IF NOT EXISTS leagues (code TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS league_matches (id TEXT PRIMARY KEY, league_code TEXT NOT NULL, season TEXT, matchday INTEGER, kickoff TEXT, status TEXT NOT NULL, home_team_id TEXT, home_team TEXT NOT NULL, away_team_id TEXT, away_team TEXT NOT NULL, home_score INTEGER, away_score INTEGER, winner TEXT, updated_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_league_matches_date ON league_matches(league_code, kickoff);
CREATE TABLE IF NOT EXISTS league_standings (league_code TEXT NOT NULL, team_id TEXT NOT NULL, team TEXT NOT NULL, position INTEGER, played INTEGER, won INTEGER, drawn INTEGER, lost INTEGER, goal_difference INTEGER, points INTEGER, updated_at TEXT NOT NULL, PRIMARY KEY(league_code, team_id));
CREATE TABLE IF NOT EXISTS league_predictions (id BIGSERIAL PRIMARY KEY, match_id TEXT NOT NULL UNIQUE, league_code TEXT NOT NULL, model_name TEXT NOT NULL, model_version TEXT NOT NULL, created_at TEXT NOT NULL, locked_at TEXT NOT NULL DEFAULT '', home_probability DOUBLE PRECISION NOT NULL, draw_probability DOUBLE PRECISION NOT NULL, away_probability DOUBLE PRECISION NOT NULL, predicted_outcome TEXT NOT NULL, confidence TEXT NOT NULL, feature_json TEXT NOT NULL, actual_outcome TEXT, correct INTEGER, evaluated_at TEXT, prediction_status TEXT NOT NULL DEFAULT 'provisional', generated_at TEXT);
CREATE TABLE IF NOT EXISTS team_ratings (league_code TEXT NOT NULL, team TEXT NOT NULL, rating DOUBLE PRECISION NOT NULL, as_of TEXT NOT NULL, PRIMARY KEY(league_code, team));
CREATE TABLE IF NOT EXISTS model_metrics (id BIGSERIAL PRIMARY KEY, league_code TEXT NOT NULL, model_name TEXT NOT NULL, created_at TEXT NOT NULL, split_type TEXT NOT NULL, metrics_json TEXT NOT NULL);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_utc(value: str) -> datetime:
    parsed=datetime.fromisoformat(value.replace("Z","+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class _PostgresConnection:
    def __init__(self, raw: Any):
        self.raw = raw

    @staticmethod
    def _sql(query: str) -> str:
        return query.replace("?", "%s")

    def execute(self, query: str, params: tuple[Any, ...] = ()):
        return self.raw.execute(self._sql(query), params)

    def executemany(self, query: str, params: Any):
        return self.raw.cursor().executemany(self._sql(query), params)


class Database:
    def __init__(self, path: Path, database_url: str | None = None, initialize_schema: bool = True):
        self.path = path
        self.database_url = database_url or os.getenv("DATABASE_URL") or None
        self.backend = "postgres" if self.database_url else "sqlite"
        if self.backend == "sqlite":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        if initialize_schema:
            self.initialize()

    @contextmanager
    def connect(self) -> Iterator[Any]:
        if self.backend=="postgres":
            try:
                import psycopg
                from psycopg.rows import dict_row
            except ImportError as exc: raise RuntimeError("Postgres requires psycopg[binary]") from exc
            raw=psycopg.connect(self.database_url,row_factory=dict_row,prepare_threshold=None)
            connection: Any=_PostgresConnection(raw)
        else:
            raw=sqlite3.connect(self.path,timeout=10); raw.row_factory=sqlite3.Row
            raw.execute("PRAGMA journal_mode = MEMORY"); raw.execute("PRAGMA temp_store = MEMORY"); raw.execute("PRAGMA foreign_keys = ON")
            connection=raw
        try:
            yield connection
            raw.commit()
        except Exception:
            raw.rollback()
            raise
        finally:
            raw.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            if self.backend=="postgres":
                for statement in POSTGRES_SCHEMA.split(";"):
                    if statement.strip(): connection.execute(statement)
                connection.execute("ALTER TABLE league_matches ADD COLUMN IF NOT EXISTS winner TEXT")
                connection.execute("ALTER TABLE league_predictions ADD COLUMN IF NOT EXISTS prediction_status TEXT NOT NULL DEFAULT 'provisional'")
                connection.execute("ALTER TABLE league_predictions ADD COLUMN IF NOT EXISTS generated_at TEXT")
            else:
                connection.executescript(SCHEMA)
            if self.backend=="sqlite":
                columns={row[1] for row in connection.execute("PRAGMA table_info(league_matches)")}
                if "winner" not in columns: connection.execute("ALTER TABLE league_matches ADD COLUMN winner TEXT")
                prediction_columns={row[1] for row in connection.execute("PRAGMA table_info(league_predictions)")}
                if "prediction_status" not in prediction_columns: connection.execute("ALTER TABLE league_predictions ADD COLUMN prediction_status TEXT NOT NULL DEFAULT 'provisional'")
                if "generated_at" not in prediction_columns: connection.execute("ALTER TABLE league_predictions ADD COLUMN generated_at TEXT")
            # One-time lifecycle migration: preserve evaluated rows, retain picks
            # already inside 48h, and make the remaining legacy season-long
            # locks refreshable provisional previews.
            now=datetime.now(timezone.utc); cutoff=now+timedelta(hours=48)
            legacy=connection.execute("""SELECT p.id,p.created_at,p.evaluated_at,m.kickoff FROM league_predictions p JOIN league_matches m ON m.id=p.match_id WHERE p.generated_at IS NULL""").fetchall()
            for row in legacy:
                kickoff=parse_utc(row["kickoff"]) if row["kickoff"] else now
                status="evaluated" if row["evaluated_at"] else "locked" if kickoff<=cutoff else "provisional"
                locked_at=row["created_at"] if status=="locked" else ""
                connection.execute("UPDATE league_predictions SET prediction_status=?,generated_at=?,locked_at=? WHERE id=?",(status,row["created_at"],locked_at,row["id"]))

    def upsert_teams(self, strengths: dict[str, float]) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO teams(name, base_strength, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET base_strength=excluded.base_strength, updated_at=excluded.updated_at""",
                [(name, strength, now) for name, strength in strengths.items()],
            )

    def upsert_matches(self, matches: list[dict[str, Any]]) -> tuple[int, int]:
        inserted = updated = 0
        now = utc_now()
        with self.connect() as connection:
            for match in matches:
                old = connection.execute("SELECT * FROM matches WHERE id=?", (match["id"],)).fetchone()
                if old and old["status"] == "completed" and match.get("status") != "completed":
                    continue  # a stale feed may never undo an actual result
                values = (
                    match["id"], match["stage"], match.get("match_number", 0), match.get("kickoff"),
                    match.get("status", "scheduled"), match.get("home_team"), match.get("away_team"),
                    match.get("home_score"), match.get("away_score"), match.get("home_penalties"),
                    match.get("away_penalties"), match.get("winner_team"), match.get("home_source_match"),
                    match.get("away_source_match"), now,
                )
                connection.execute(
                    """INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET stage=excluded.stage, match_number=excluded.match_number,
                    kickoff=excluded.kickoff, status=excluded.status, home_team=excluded.home_team,
                    away_team=excluded.away_team, home_score=excluded.home_score, away_score=excluded.away_score,
                    home_penalties=excluded.home_penalties, away_penalties=excluded.away_penalties,
                    winner_team=excluded.winner_team, home_source_match=COALESCE(excluded.home_source_match, matches.home_source_match),
                    away_source_match=COALESCE(excluded.away_source_match, matches.away_source_match), updated_at=excluded.updated_at""",
                    values,
                )
                inserted += old is None
                updated += old is not None
                for team in (match.get("home_team"), match.get("away_team")):
                    if team:
                        connection.execute(
                            "INSERT INTO teams(name, base_strength, updated_at) VALUES (?, 50, ?) ON CONFLICT(name) DO NOTHING",
                            (team, now),
                        )
        return inserted, updated

    def rows(self, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def get_matches(self) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM matches ORDER BY match_number, kickoff, id")

    def get_teams(self) -> list[dict[str, Any]]:
        return self.rows("SELECT * FROM teams ORDER BY name")

    def retain_matches(self, match_ids: set[str]) -> int:
        """Make a successful complete feed authoritative, removing stale feed rows."""
        if not match_ids:
            raise ValueError("Refusing to prune matches for an empty feed")
        placeholders = ",".join("?" for _ in match_ids)
        with self.connect() as connection:
            cursor = connection.execute(
                f"DELETE FROM matches WHERE id NOT IN ({placeholders})",
                tuple(sorted(match_ids)),
            )
            return int(cursor.rowcount)

    def reset_team_statuses(self, eliminated: set[str], active: set[str]) -> None:
        now = utc_now()
        with self.connect() as connection:
            connection.execute("UPDATE teams SET eliminated=0, qualified=0, updated_at=?", (now,))
            connection.executemany("UPDATE teams SET eliminated=1, qualified=0, updated_at=? WHERE name=?", [(now, t) for t in eliminated])
            connection.executemany("UPDATE teams SET eliminated=0, qualified=1, updated_at=? WHERE name=?", [(now, t) for t in active])

    def start_run(self, command: str) -> int:
        with self.connect() as connection:
            if self.backend=="postgres":
                row=connection.execute("INSERT INTO agent_runs(started_at, command, status) VALUES (?, ?, 'running') RETURNING id",(utc_now(),command)).fetchone()
                return int(row["id"])
            cursor=connection.execute("INSERT INTO agent_runs(started_at, command, status) VALUES (?, ?, 'running')",(utc_now(),command))
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, details: str = "") -> None:
        with self.connect() as connection:
            connection.execute(
                "UPDATE agent_runs SET completed_at=?, status=?, details=? WHERE id=?",
                (utc_now(), status, details, run_id),
            )

    def save_predictions(self, run_id: int, bracket: list[dict[str, Any]]) -> None:
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO predictions(run_id, match_id, stage, home_team, away_team, predicted_winner,
                home_score, away_score, basis) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(
                    run_id, item["match_id"], item["stage"], item.get("home_team"), item.get("away_team"),
                    item.get("winner"), item.get("home_model_score"), item.get("away_model_score"), item["basis"],
                ) for item in bracket],
            )

    def save_snapshot(self, run_id: int, bracket: list[dict[str, Any]], winner: str | None, summary: str) -> None:
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO bracket_snapshots(run_id, created_at, predicted_winner, bracket_json, change_summary)
                VALUES (?, ?, ?, ?, ?)""",
                (run_id, utc_now(), winner, json.dumps(bracket, ensure_ascii=False), summary),
            )

    def latest_snapshots(self, limit: int = 2) -> list[dict[str, Any]]:
        rows = self.rows("SELECT * FROM bracket_snapshots ORDER BY id DESC LIMIT ?", (limit,))
        for row in rows:
            row["bracket"] = json.loads(row.pop("bracket_json"))
        return rows

    def upsert_league_matches(self, league: Any, matches: list[dict[str, Any]]) -> tuple[int, int]:
        now = utc_now(); inserted = updated = 0
        with self.connect() as c:
            c.execute("INSERT INTO leagues VALUES(?,?,?,?) ON CONFLICT(code) DO UPDATE SET name=excluded.name,country=excluded.country,updated_at=excluded.updated_at", (league.code, league.name, league.country, now))
            for m in matches:
                old = c.execute("SELECT status FROM league_matches WHERE id=?", (m["id"],)).fetchone()
                if old and old["status"] == "completed" and m["status"] != "completed": continue
                c.execute("""INSERT INTO league_matches(id,league_code,season,matchday,kickoff,status,home_team_id,home_team,away_team_id,away_team,home_score,away_score,winner,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET season=excluded.season,matchday=excluded.matchday,kickoff=excluded.kickoff,status=excluded.status,home_team_id=excluded.home_team_id,home_team=excluded.home_team,away_team_id=excluded.away_team_id,away_team=excluded.away_team,home_score=excluded.home_score,away_score=excluded.away_score,winner=excluded.winner,updated_at=excluded.updated_at""", (m["id"],m["league_code"],m.get("season"),m.get("matchday"),m.get("kickoff"),m["status"],m.get("home_team_id"),m["home_team"],m.get("away_team_id"),m["away_team"],m.get("home_score"),m.get("away_score"),m.get("winner"),now))
                inserted += old is None; updated += old is not None
        return inserted, updated

    def league_matches(self, code: str, date: str | None = None) -> list[dict[str, Any]]:
        q = "SELECT m.*,p.home_probability,p.draw_probability,p.away_probability,p.predicted_outcome,p.confidence,p.correct,p.prediction_status,p.generated_at,p.locked_at,p.model_version FROM league_matches m LEFT JOIN league_predictions p ON p.match_id=m.id WHERE m.league_code=?"
        params: tuple[Any,...] = (code,)
        if date: q += " AND substr(m.kickoff,1,10)=?"; params += (date,)
        return self.rows(q+" ORDER BY m.kickoff,m.id", params)

    def save_league_prediction(self, match_id: str, code: str, probabilities: dict[str,float], features: list[float], model_name: str="logistic-regression", model_version: str="v2", lock_window_hours: int=48, now: datetime | None=None) -> str | None:
        current=now or datetime.now(timezone.utc); now_text=current.isoformat(timespec="seconds"); pick=max(probabilities,key=probabilities.get); confidence="High" if probabilities[pick]>=.65 else "Medium" if probabilities[pick]>=.50 else "Low"
        with self.connect() as c:
            match=c.execute("SELECT kickoff,status FROM league_matches WHERE id=?",(match_id,)).fetchone()
            if not match: raise ValueError("Unknown match")
            kickoff=parse_utc(match["kickoff"]) if match["kickoff"] else None
            if match["status"]=="completed" or not kickoff or kickoff<=current: return None
            target="locked" if kickoff<=current+timedelta(hours=lock_window_hours) else "provisional"
            existing=c.execute("SELECT prediction_status FROM league_predictions WHERE match_id=?",(match_id,)).fetchone()
            if existing and existing["prediction_status"] in {"locked","evaluated"}: return None
            locked_at=now_text if target=="locked" else ""
            if existing:
                c.execute("""UPDATE league_predictions SET model_name=?,model_version=?,generated_at=?,locked_at=?,home_probability=?,draw_probability=?,away_probability=?,predicted_outcome=?,confidence=?,feature_json=?,prediction_status=? WHERE match_id=? AND prediction_status='provisional'""",(model_name,model_version,now_text,locked_at,probabilities["H"],probabilities["D"],probabilities["A"],pick,confidence,json.dumps(features),target,match_id))
            else:
                c.execute("""INSERT INTO league_predictions(match_id,league_code,model_name,model_version,created_at,locked_at,home_probability,draw_probability,away_probability,predicted_outcome,confidence,feature_json,prediction_status,generated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(match_id,code,model_name,model_version,now_text,locked_at,probabilities["H"],probabilities["D"],probabilities["A"],pick,confidence,json.dumps(features),target,now_text))
            return target

    def lock_league_prediction(self, match_id: str, code: str, probabilities: dict[str,float], features: list[float], model_name: str="logistic-regression", model_version: str="v2") -> bool:
        """Compatibility helper for callers explicitly requesting an official pick."""
        return self.save_league_prediction(match_id,code,probabilities,features,model_name,model_version,lock_window_hours=10**6)=="locked"

    def evaluate_league_predictions(self, code: str) -> int:
        now=utc_now(); count=0
        with self.connect() as c:
            rows=c.execute("""SELECT p.id,p.predicted_outcome,m.home_score,m.away_score FROM league_predictions p JOIN league_matches m ON m.id=p.match_id WHERE p.league_code=? AND p.prediction_status='locked' AND p.evaluated_at IS NULL AND m.status='completed'""",(code,)).fetchall()
            for r in rows:
                actual="H" if r["home_score"]>r["away_score"] else "A" if r["home_score"]<r["away_score"] else "D"
                c.execute("UPDATE league_predictions SET actual_outcome=?,correct=?,evaluated_at=?,prediction_status='evaluated' WHERE id=?",(actual,int(actual==r["predicted_outcome"]),now,r["id"])); count+=1
        return count

    def league_metrics(self, code: str) -> dict[str, Any]:
        rows=self.rows("SELECT * FROM league_predictions WHERE league_code=? AND prediction_status='evaluated' AND evaluated_at IS NOT NULL ORDER BY evaluated_at",(code,)); total=len(rows); correct=sum(r["correct"] for r in rows)
        by={o:[r for r in rows if r["predicted_outcome"]==o] for o in "HDA"}; high=[r for r in rows if r["confidence"]=="High"]; last=rows[-10:]
        rate=lambda rs: (sum(r["correct"] for r in rs)/len(rs) if rs else None)
        return {"total":total,"correct":correct,"accuracy":rate(rows),"home_accuracy":rate(by["H"]),"draw_accuracy":rate(by["D"]),"away_accuracy":rate(by["A"]),"high_confidence_accuracy":rate(high),"last_10_correct":sum(r["correct"] for r in last),"last_10_total":len(last)}
