from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
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
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        # MEMORY avoids fragile journal-file rename/locking behaviour in restricted
        # Windows demo environments. Transactions still protect each operation.
        connection.execute("PRAGMA journal_mode = MEMORY")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)

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
                            "INSERT OR IGNORE INTO teams(name, base_strength, updated_at) VALUES (?, 50, ?)",
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
            cursor = connection.execute(
                "INSERT INTO agent_runs(started_at, command, status) VALUES (?, ?, 'running')", (utc_now(), command)
            )
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
