from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.database import Database

TABLES = (
    "teams",
    "matches",
    "agent_runs",
    "predictions",
    "bracket_snapshots",
    "leagues",
    "league_matches",
    "league_standings",
    "league_predictions",
    "team_ratings",
    "model_metrics",
)
SEQUENCED_TABLES = ("agent_runs", "predictions", "bracket_snapshots", "league_predictions", "model_metrics")


def migrate(source_path: Path, database_url: str) -> dict[str, dict[str, int]]:
    if not source_path.exists():
        raise FileNotFoundError(f"SQLite source does not exist: {source_path}")
    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    target = Database(Path("data/postgres-placeholder.db"), database_url)
    report: dict[str, dict[str, int]] = {}
    try:
        with target.connect() as destination:
            for table in TABLES:
                exists = source.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone()
                if not exists:
                    report[table] = {"source": 0, "inserted": 0, "target": 0}
                    continue
                rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
                before = int(destination.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()["count"])
                if rows:
                    columns = list(rows[0].keys())
                    names = ",".join(f'"{name}"' for name in columns)
                    placeholders = ",".join("?" for _ in columns)
                    destination.executemany(
                        f'INSERT INTO "{table}" ({names}) VALUES ({placeholders}) ON CONFLICT DO NOTHING',
                        [tuple(row[name] for name in columns) for row in rows],
                    )
                after = int(destination.execute(f'SELECT COUNT(*) AS count FROM "{table}"').fetchone()["count"])
                if after < len(rows):
                    raise RuntimeError(
                        f"Migration verification failed for {table}: source={len(rows)}, target={after}"
                    )
                report[table] = {"source": len(rows), "inserted": after - before, "target": after}
            for table in SEQUENCED_TABLES:
                destination.execute(
                    f"SELECT setval(pg_get_serial_sequence('{table}','id'),"
                    f"COALESCE(MAX(id),1),MAX(id) IS NOT NULL) FROM {table}"
                )
    finally:
        source.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Idempotently copy SportsIntelAI SQLite state to PostgreSQL")
    parser.add_argument("--source", type=Path, default=Path(os.getenv("DATABASE_PATH", "data/fifa2026.db")))
    args = parser.parse_args()
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        parser.error("DATABASE_URL is required")
    for table, counts in migrate(args.source, database_url).items():
        print(f"{table}: source={counts['source']} inserted={counts['inserted']} target={counts['target']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
