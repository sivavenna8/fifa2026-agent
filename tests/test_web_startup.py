from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from src.web_app import _bootstrap_dashboard


class DashboardStartupTests(unittest.TestCase):
    def test_empty_database_fetches_and_builds_initial_snapshot(self):
        db = Mock()
        db.get_matches.return_value = []
        db.latest_snapshots.return_value = []
        with patch("main.fetch_data") as fetch, patch("main.rebuild") as rebuild:
            _bootstrap_dashboard(db)
        fetch.assert_called_once_with(db)
        rebuild.assert_called_once_with(db, "web-startup", print_table=False)

    def test_existing_database_does_not_fetch_or_rebuild(self):
        db = Mock()
        db.get_matches.return_value = [{"id": "match"}]
        db.latest_snapshots.return_value = [{"id": 1}]
        with patch("main.fetch_data") as fetch, patch("main.rebuild") as rebuild:
            _bootstrap_dashboard(db)
        fetch.assert_not_called()
        rebuild.assert_not_called()


if __name__ == "__main__":
    unittest.main()
