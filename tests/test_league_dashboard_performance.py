from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from src.database import Database


ROOT = Path(__file__).resolve().parent.parent


class _CountingConnection:
    def __init__(self, connection: Any, database: "CountingDatabase"):
        self.connection = connection
        self.database = database

    def execute(self, query: str, params: tuple[Any, ...] = ()):
        self.database.query_count += 1
        return self.connection.execute(query, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.connection, name)


class CountingDatabase(Database):
    def __init__(self, path: Path):
        self.connection_count = 0
        self.query_count = 0
        super().__init__(path)
        self.connection_count = 0
        self.query_count = 0

    @contextmanager
    def connect(self):
        self.connection_count += 1
        with super().connect() as connection:
            yield _CountingConnection(connection, self)


class LeagueDashboardPerformanceTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", dir=ROOT / "data", delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.db = CountingDatabase(self.path)

    def tearDown(self):
        self.path.unlink(missing_ok=True)

    def _insert_match(self, match_id: str, kickoff: str, updated_at: str = "2026-08-29T09:00:00Z") -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO league_matches(
                    id,league_code,season,matchday,kickoff,status,home_team_id,home_team,
                    away_team_id,away_team,home_score,away_score,winner,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (match_id,"PL","2026",1,kickoff,"scheduled","H","Home FC","A","Away FC",None,None,None,updated_at),
            )

    def _insert_prediction(
        self,
        index: int,
        *,
        status: str,
        correct: int,
        confidence: str,
        outcome: str,
        evaluated_at: str | None,
    ) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO league_predictions(
                    match_id,league_code,model_name,model_version,created_at,locked_at,
                    home_probability,draw_probability,away_probability,predicted_outcome,
                    confidence,feature_json,actual_outcome,correct,evaluated_at,prediction_status,generated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    f"prediction-{index}","PL","test","v1","2026-08-01T00:00:00Z","2026-08-01T00:00:00Z",
                    .5,.3,.2,outcome,confidence,"[]",outcome if correct else "D",correct,evaluated_at,status,
                    "2026-08-01T00:00:00Z",
                ),
            )

    def test_dashboard_uses_one_connection_and_five_queries(self):
        self._insert_match("start", "2026-08-29T00:00:00Z")
        self._insert_match("end", "2026-08-29T23:59:59Z")
        self._insert_match("next", "2026-08-30T00:00:00Z")
        self.db.connection_count = self.db.query_count = 0

        dashboard = self.db.league_dashboard_data("PL", "2026-08-29")

        self.assertEqual(self.db.connection_count, 1)
        self.assertEqual(self.db.query_count, 5)
        self.assertEqual([match["id"] for match in dashboard["matches"]], ["start", "end"])
        self.assertEqual(dashboard["next_matchday"], "2026-08-30")

    def test_range_filter_matches_existing_utc_date_semantics(self):
        self._insert_match("previous", "2026-08-28T23:59:59Z")
        self._insert_match("first", "2026-08-29T00:00:00+00:00")
        self._insert_match("last", "2026-08-29T23:59:59Z")
        self._insert_match("following", "2026-08-30T00:00:00+00:00")

        self.assertEqual(
            [match["id"] for match in self.db.league_matches("PL", "2026-08-29")],
            ["first", "last"],
        )

    def test_sql_metrics_match_previous_definitions(self):
        evaluated = []
        for index in range(12):
            row = {
                "correct": int(index % 3 != 0),
                "confidence": "High" if index % 2 == 0 else "Medium",
                "outcome": "HDA"[index % 3],
                "evaluated_at": f"2026-08-{index + 1:02d}T12:00:00Z",
            }
            evaluated.append(row)
            self._insert_prediction(index, status="evaluated", **row)
        self._insert_prediction(100, status="provisional", correct=1, confidence="High", outcome="H", evaluated_at="2026-08-20T12:00:00Z")
        self._insert_prediction(101, status="locked", correct=1, confidence="High", outcome="H", evaluated_at="2026-08-21T12:00:00Z")
        self._insert_prediction(102, status="evaluated", correct=1, confidence="High", outcome="H", evaluated_at=None)

        metrics = self.db.league_metrics("PL")
        high = [row for row in evaluated if row["confidence"] == "High"]
        by_outcome = {outcome: [row for row in evaluated if row["outcome"] == outcome] for outcome in "HDA"}

        self.assertEqual(metrics["total"], len(evaluated))
        self.assertEqual(metrics["correct"], sum(row["correct"] for row in evaluated))
        self.assertEqual(metrics["accuracy"], sum(row["correct"] for row in evaluated) / len(evaluated))
        self.assertEqual(metrics["high_confidence_accuracy"], sum(row["correct"] for row in high) / len(high))
        self.assertEqual(metrics["home_accuracy"], sum(row["correct"] for row in by_outcome["H"]) / len(by_outcome["H"]))
        self.assertEqual(metrics["draw_accuracy"], sum(row["correct"] for row in by_outcome["D"]) / len(by_outcome["D"]))
        self.assertEqual(metrics["away_accuracy"], sum(row["correct"] for row in by_outcome["A"]) / len(by_outcome["A"]))
        self.assertEqual(metrics["last_10_total"], 10)
        self.assertEqual(metrics["last_10_correct"], sum(row["correct"] for row in evaluated[-10:]))

    def test_dashboard_indexes_are_created_idempotently(self):
        self.db.initialize()
        indexes = {
            row["name"] for row in self.db.rows(
                "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
            )
        }
        self.assertIn("idx_league_matches_date", indexes)
        self.assertIn("idx_league_predictions_evaluated", indexes)
        self.assertIn("idx_model_metrics_latest", indexes)


if __name__ == "__main__":
    unittest.main()
