from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.database import Database
from src.league_config import get_league
from src.league_service import daily_league

ROOT = Path(__file__).resolve().parent.parent


class RefreshAutomationTests(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix='.db', dir=ROOT / 'data', delete=False)
        handle.close()
        path = Path(handle.name)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        self.db = Database(path)
        now = datetime.now(timezone.utc)
        self.fixtures = [dict(id=mid, league_code='PL', season='2026',
            kickoff=(now + timedelta(hours=24)).isoformat(), status='scheduled',
            home_team_id='H', home_team='Home', away_team_id='A', away_team='Away',
            home_score=None, away_score=None) for mid in ('1', '2')]
        self.db.upsert_league_matches(get_league('PL'), self.fixtures)
        self.db.save_league_prediction('2', 'PL', {'H': .2, 'D': .6, 'A': .2}, [])
        self.fixtures[1].update(status='completed', home_score=1, away_score=1)
        settings = SimpleNamespace(api_key='not-a-real-key', api_url='', request_timeout=1)
        model = SimpleNamespace(model_name='test', predict=Mock(return_value={'H': .7, 'D': .2, 'A': .1}))
        for target, value in [('src.league_service.get_settings', settings),
                              ('src.league_service.LeagueAPIClient.matches', self.fixtures),
                              ('src.league_service.LeagueAPIClient.standings', []),
                              ('src.league_service.LeagueModel.load', model)]:
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # All ingestion/model boundaries are mocked; no real API or Telegram.
        sender = patch('src.telegram_bot.urlopen', side_effect=AssertionError('Real sending forbidden'))
        sender.start()
        self.addCleanup(sender.stop)

    def predictions(self):
        return self.db.rows('SELECT * FROM league_predictions ORDER BY match_id')

    def runs(self):
        return self.db.rows("SELECT * FROM agent_runs WHERE command='league-daily:PL' ORDER BY id")

    def test_duplicate_dispatch_cycles_preserve_locked_and_evaluated_records(self):
        first = daily_league(self.db, 'PL')
        original = self.predictions()
        with patch('src.league_service.LeagueModel.load', return_value=SimpleNamespace(
                model_name='different', predict=Mock(return_value={'H': .1, 'D': .1, 'A': .8}))):
            second = daily_league(self.db, 'PL')
            third = daily_league(self.db, 'PL')
        self.assertEqual(original, self.predictions())
        self.assertEqual(first['evaluated'], 1)
        self.assertEqual(second['evaluated'], 0)
        self.assertEqual(third['evaluated'], 0)
        self.assertEqual(len(self.db.league_matches('PL')), 2)
        self.assertEqual([r['status'] for r in self.runs()], ['success'] * 3)

    def test_pipeline_success_marker_follows_prediction_and_evaluation(self):
        report = daily_league(self.db, 'PL')
        run = self.runs()[0]
        self.assertEqual(run['status'], 'success')
        self.assertTrue(run['completed_at'])
        self.assertEqual(json.loads(run['details']), report)
        fixture = self.db.rows("SELECT * FROM agent_runs WHERE command='league-fixtures-refresh:PL'")[0]
        self.assertEqual(fixture['status'], 'success')

    def test_prediction_failure_does_not_claim_complete_pipeline_success(self):
        with patch('src.league_service.predict_league', side_effect=ValueError('sensitive connection detail')):
            with self.assertRaises(ValueError):
                daily_league(self.db, 'PL')
        self.assertEqual(self.runs()[0]['status'], 'failed')
        self.assertNotIn('sensitive', self.runs()[0]['details'])
        fixture = self.db.rows("SELECT * FROM agent_runs WHERE command='league-fixtures-refresh:PL'")[0]
        self.assertEqual(fixture['status'], 'success')

    def test_failed_cycle_recovers_without_reevaluating_official_results(self):
        with patch('src.league_service.predict_league', side_effect=ValueError('retry')):
            with self.assertRaises(ValueError):
                daily_league(self.db, 'PL')
        evaluated = self.predictions()[0]
        result = daily_league(self.db, 'PL')
        self.assertEqual(result['evaluated'], 0)
        self.assertEqual(self.db.rows("SELECT * FROM league_predictions WHERE match_id='2'")[0], evaluated)
        self.assertEqual([r['status'] for r in self.runs()], ['failed', 'success'])

    def test_fetch_failure_is_failed_and_creates_no_successful_fixture_marker(self):
        with patch('src.league_service.LeagueAPIClient.matches', side_effect=ValueError('unavailable')):
            with self.assertRaises(ValueError):
                daily_league(self.db, 'PL')
        self.assertEqual(self.runs()[0]['status'], 'failed')
        self.assertFalse(self.db.rows("SELECT id FROM agent_runs WHERE command='league-fixtures-refresh:PL' AND status='success'"))

    def test_workflow_serializes_all_triggers_and_has_no_telegram_credentials(self):
        workflow = (ROOT / '.github/workflows/sportsintel-league.yml').read_text()
        self.assertIn('group: sportsintel-league-agent', workflow)
        self.assertIn('cancel-in-progress: false', workflow)
        self.assertIn('contents: read', workflow)
        self.assertIn('timeout-minutes: 15', workflow)
        self.assertIn('run: python main.py league-daily PL', workflow)
        self.assertIn('TELEGRAM_PUBLISHER_ENABLED: "false"', workflow)
        self.assertIn('FIFA_TELEGRAM_ENABLED: "false"', workflow)
        self.assertNotIn('TELEGRAM_BOT_TOKEN', workflow)
        # Retain the existing fallback until the replacement has been tested.
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn('schedule:', workflow)


if __name__ == '__main__':
    unittest.main()
