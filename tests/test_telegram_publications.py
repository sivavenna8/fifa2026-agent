from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.database import Database
from src.league_config import get_league
from src.league_publications import LONDON, publish, result_day, snapshot, tick, window
from src.league_telegram import units
from src.telegram_bot import TelegramError
from src.telegram_worker import router

ROOT = Path(__file__).resolve().parent.parent
MORNING = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)  # 09:00 BST
EVENING = datetime(2026, 10, 10, 22, 30, tzinfo=timezone.utc)
MIGRATION = ROOT / 'sql' / 'telegram_publications_v2.sql'


class PublicationTests(unittest.TestCase):
    def setUp(self):
        enabled_patch = patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED='true')
        enabled_patch.start()
        self.addCleanup(enabled_patch.stop)
        handle = tempfile.NamedTemporaryFile(suffix='.db', dir=ROOT / 'data', delete=False)
        handle.close()
        self.path = Path(handle.name)
        self.db = Database(self.path)
        with self.db.connect() as c: c.executescript(MIGRATION.read_text())
        self.settings_patch = patch('src.league_publications.get_settings', return_value=SimpleNamespace(
            telegram_token='test-token', telegram_chat_id='test-channel', request_timeout=15))
        self.settings_patch.start()
        self.send_patch = patch('src.league_publications.send_message', return_value={'result': {'message_id': 123}})
        self.send = self.send_patch.start()
        self.refresh(MORNING)

    def tearDown(self):
        self.send_patch.stop()
        self.settings_patch.stop()
        self.path.unlink(missing_ok=True)

    def refresh(self, now):
        with self.db.connect() as c:
            stamp = now.isoformat()
            c.execute("INSERT INTO agent_runs(started_at,completed_at,command,status,details) VALUES(?,?,'league-fixtures-refresh:PL','success','{}')", (stamp, stamp))
            c.execute('UPDATE league_matches SET updated_at=?', (stamp,))

    def fixture(self, mid='1', lifecycle='locked', status='scheduled', kickoff='2026-10-10T14:00:00Z', home_score=None, away_score=None):
        match = dict(id=mid, league_code='PL', season='2026', kickoff=kickoff, status='scheduled',
                     home_team=f'Arsenal {mid}', away_team='Chelsea')
        self.db.upsert_league_matches(get_league('PL'), [match])
        if lifecycle:
            self.db.save_league_prediction(mid, 'PL', {'H': .582, 'D': .247, 'A': .171}, [], now=MORNING-timedelta(hours=12))
            with self.db.connect() as c: c.execute('UPDATE league_predictions SET prediction_status=? WHERE match_id=?', (lifecycle, mid))
        with self.db.connect() as c:
            c.execute('UPDATE league_matches SET status=?,home_score=?,away_score=?,updated_at=? WHERE id=?', (status, home_score, away_score, MORNING.isoformat(), mid))

    def run_pub(self, publication='MORNING', **kwargs):
        return publish(self.db, publication=publication, now=kwargs.pop('now', MORNING if publication=='MORNING' else EVENING), **kwargs)

    def records(self):
        return self.db.rows("SELECT * FROM agent_runs WHERE command LIKE 'PL:%'")

    def test_disabled_missing_invalid_and_recovery_never_write_or_send(self):
        self.fixture(status='completed', home_score=2, away_score=1)
        before = self.path.read_bytes()
        for value in ['false', '', 'invalid']:
            with patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED=value):
                for publication, recover in [('MORNING', False), ('RESULTS', False), ('RESULTS', True)]:
                    self.assertEqual(self.run_pub(publication, recover=recover)['status'], 'disabled')
        with patch.dict(os.environ):
            os.environ.pop('TELEGRAM_PUBLISHER_ENABLED', None)
            self.assertEqual(self.run_pub()['status'], 'disabled')
        self.assertEqual(self.path.read_bytes(), before)
        self.send.assert_not_called()

    def test_disabled_preview_remains_read_only(self):
        self.fixture()
        before = self.path.read_bytes()
        with patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED='false'), redirect_stdout(io.StringIO()):
            self.assertEqual(self.run_pub(dry_run=True)['status'], 'dry-run')
            self.assertEqual(self.run_pub('RESULTS', dry_run=True)['status'], 'dry-run')
        self.assertEqual(self.path.read_bytes(), before)
        self.send.assert_not_called()

    def test_cli_and_actual_manual_workflow_command_cannot_bypass_switch(self):
        import main
        import textwrap
        settings = SimpleNamespace(database_path=self.path, database_url=None)
        def invoke(command):
            with patch('sys.argv', command[1:]):
                return main.main()
        workflow = (ROOT/'.github/workflows/league_telegram.yml').read_text()
        self.assertIn("vars.TELEGRAM_PUBLISHER_ENABLED || 'false'", workflow)
        script = textwrap.dedent(workflow.split("python - <<'PY'\n", 1)[1].rsplit('          PY', 1)[0])
        with patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED='false', PUBLICATION_KIND='RESULTS',
                        PUBLICATION_DATE='2026-10-10', PUBLICATION_DRY_RUN='false'), patch('main.get_settings', return_value=settings):
            for flags in [[], ['--scheduled'], ['--publication', 'RESULTS', '--recover']]:
                self.assertEqual(invoke(['python', 'main.py', 'league-telegram', 'PL'] + flags), 1)
            with patch('subprocess.call', side_effect=invoke):
                with self.assertRaises(SystemExit) as exit_code:
                    exec(compile(script, 'manual-workflow', 'exec'), {})
                self.assertEqual(exit_code.exception.code, 1)
        self.assertEqual(self.records(), [])
        self.send.assert_not_called()

    def test_switch_rechecked_before_send_and_between_parts(self):
        self.fixture()
        with patch('src.league_publications.publishing_enabled', side_effect=[True, True, False]):
            self.assertEqual(self.run_pub()['status'], 'disabled')
        self.assertEqual(self.records(), [])
        self.send.assert_not_called()
        for i in range(35): self.fixture(str(i))
        def disable_after_send(*args):
            os.environ['TELEGRAM_PUBLISHER_ENABLED'] = 'false'
            return {'result': {'message_id': 123}}
        self.send.side_effect = disable_after_send
        outcome = self.run_pub()
        self.assertEqual((outcome['status'], outcome['sent_parts']), ('disabled', 1))
        self.assertEqual(self.records()[0]['status'], 'running')
        self.assertEqual(len(json.loads(self.records()[0]['details'])['delivered']), 1)
        self.send.assert_called_once()

    def test_completed_reschedule_scores_only_current_day_preserves_history(self):
        self.fixture()
        self.run_pub()
        morning = self.records()[0]
        predictions = self.db.rows('SELECT * FROM league_predictions')
        with self.db.connect() as c:
            c.execute("UPDATE league_matches SET kickoff='2026-10-11T14:00:00Z',status='completed',home_score=2,away_score=1 WHERE id='1'")
        self.refresh(EVENING)
        self.run_pub('RESULTS')
        old = self.send.call_args.args[2]
        self.assertIn('Rescheduled · Not scored', old)
        self.assertIn('Daily official picks: 0/0', old)
        next_evening = EVENING + timedelta(days=1)
        self.refresh(next_evening)
        self.run_pub('RESULTS', now=next_evening)
        self.assertIn('Daily official picks: 1/1', self.send.call_args.args[2])
        self.assertEqual(self.run_pub('RESULTS', now=next_evening)['status'], 'already-sent')
        self.assertEqual(predictions, self.db.rows('SELECT * FROM league_predictions'))
        self.assertEqual(morning, self.db.rows('SELECT * FROM agent_runs WHERE id=?', (morning['id'],))[0])

    def test_same_day_kickoff_change_keeps_one_result(self):
        self.fixture()
        self.run_pub()
        with self.db.connect() as c:
            c.execute("UPDATE league_matches SET kickoff='2026-10-10T19:00:00Z',status='completed',home_score=2,away_score=1")
        self.refresh(EVENING)
        self.assertEqual(self.run_pub('RESULTS')['status'], 'sent')
        self.assertIn('Daily official picks: 1/1', self.send.call_args.args[2])
        self.assertEqual(self.run_pub('RESULTS')['status'], 'already-sent')

    def test_postponement_notice_does_not_reserve_future_score(self):
        self.fixture(status='postponed')
        self.refresh(EVENING)
        self.run_pub('RESULTS')
        self.assertIn('Postponed · Not scored', self.send.call_args.args[2])
        with self.db.connect() as c:
            c.execute("UPDATE league_matches SET kickoff='2026-10-11T14:00:00Z',status='completed',home_score=2,away_score=1")
        later = EVENING + timedelta(days=1)
        self.refresh(later)
        self.run_pub('RESULTS', now=later)
        self.assertIn('Daily official picks: 1/1', self.send.call_args.args[2])

    def test_confirmed_result_not_scored_again_after_date_correction(self):
        self.fixture(status='completed', home_score=2, away_score=1)
        self.refresh(EVENING)
        self.run_pub('RESULTS')
        with self.db.connect() as c:
            c.execute("UPDATE league_matches SET kickoff='2026-10-11T14:00:00Z'")
        later = EVENING + timedelta(days=1)
        self.refresh(later)
        self.run_pub('RESULTS', now=later)
        self.assertIn('Result already published · Not scored', self.send.call_args.args[2])
        self.assertNotIn('Original Official Pick', self.send.call_args.args[2])

    def test_partial_results_reserve_only_confirmed_fixture_ids(self):
        for i in range(55): self.fixture(str(i), status='completed', home_score=2, away_score=1)
        self.refresh(EVENING)
        self.assertEqual(self.run_pub('RESULTS', max_parts=1)['status'], 'partial')
        payload = json.loads(self.records()[0]['details'])
        confirmed = payload['result_fixture_ids_by_part'][0][0]
        unsent = payload['result_fixture_ids_by_part'][-1][-1]
        self.assertNotEqual(confirmed, unsent)
        with self.db.connect() as c:
            c.execute("UPDATE league_matches SET kickoff='2026-10-11T14:00:00Z' WHERE id IN (?,?)", (confirmed, unsent))
        self.assertEqual(self.run_pub('RESULTS')['status'], 'plan-changed')
        later = EVENING + timedelta(days=1)
        self.refresh(later)
        self.run_pub('RESULTS', now=later)
        text = self.send.call_args.args[2]
        self.assertIn('Result already published · Not scored', text)
        self.assertIn('Daily official picks: 1/1', text)

    def test_partial_morning_move_requires_review_but_correct_results_day_scores(self):
        for i in range(35): self.fixture(str(i))
        self.assertEqual(self.run_pub(max_parts=1)['status'], 'partial')
        before = json.loads(self.records()[0]['details'])
        with self.db.connect() as c:
            c.execute("UPDATE league_matches SET kickoff='2026-10-11T14:00:00Z' WHERE id='0'")
        self.assertEqual(self.run_pub()['status'], 'plan-changed')
        after = json.loads(self.records()[0]['details'])
        self.assertEqual(before['messages'], after['messages'])
        self.assertEqual(before['delivered'], after['delivered'])
        with self.db.connect() as c:
            c.execute("UPDATE league_matches SET status='completed',home_score=2,away_score=1 WHERE id='0'")
        later = EVENING + timedelta(days=1)
        self.refresh(later)
        self.run_pub('RESULTS', now=later)
        self.assertIn('Daily official picks: 1/1', self.send.call_args.args[2])

    def test_london_fixture_dates_at_bst_gmt_and_midnight_boundaries(self):
        cases = [('2026-03-28T23:30:00Z', '2026-03-28'),
                 ('2026-03-29T23:00:00Z', '2026-03-30'),
                 ('2026-10-24T23:00:00Z', '2026-10-25'),
                 ('2026-10-25T00:30:00Z', '2026-10-25'),
                 ('2026-10-25T01:30:00Z', '2026-10-25'),
                 ('2026-10-25T23:00:00Z', '2026-10-25'),
                 ('2026-10-26T00:00:00Z', '2026-10-26')]
        from datetime import date
        for index, (kickoff, day) in enumerate(cases):
            self.fixture(str(index), kickoff=kickoff, status='completed', home_score=1, away_score=0)
        for index, (_, day) in enumerate(cases):
            matches = snapshot(self.db, 'PL', date.fromisoformat(day), publication='RESULTS')
            self.assertIn(str(index), [m['id'] for m in matches])
            wrong_day = date.fromisoformat(day) - timedelta(days=1)
            self.assertNotIn(str(index), [m['id'] for m in snapshot(self.db, 'PL', wrong_day, publication='RESULTS')])

    def test_no_matches_sends_nothing_and_creates_no_marker(self):
        self.assertEqual(self.run_pub()['status'], 'no-matches')
        self.refresh(EVENING)
        self.assertEqual(self.run_pub('RESULTS')['status'], 'no-matches')
        self.send.assert_not_called()
        self.assertEqual(self.records(), [])

    def test_one_match_uses_exact_persisted_prediction_and_london_kickoff(self):
        self.fixture()
        before = self.db.rows('SELECT * FROM league_predictions')
        self.assertEqual(self.run_pub()['status'], 'sent')
        message = self.send.call_args.args[2]
        for expected in ['Arsenal 1 vs Chelsea', 'Home 58.2% · Draw 24.7% · Away 17.1%',
                         'Official Pick: Arsenal 1', 'Confidence: Medium', '15:00 UK']:
            self.assertIn(expected, message)
        self.assertEqual(before, self.db.rows('SELECT * FROM league_predictions'))
        self.assertEqual(self.records()[0]['command'], 'PL:2026-10-10:MORNING')

    def test_provisional_missing_invalid_official_wait(self):
        for lifecycle in ['provisional', None]:
            self.fixture(str(lifecycle), lifecycle)
        self.assertEqual(self.run_pub()['status'], 'waiting')
        with redirect_stdout(io.StringIO()) as output: self.run_pub(dry_run=True)
        self.assertIn('Provisional Pick', output.getvalue())
        self.assertIn('Prediction unavailable', output.getvalue())
        self.send.assert_not_called()

    def test_wait_then_existing_agent_locks_and_retry_sends(self):
        self.fixture(lifecycle='provisional')
        self.assertEqual(self.run_pub()['status'], 'waiting')
        with self.db.connect() as c: c.execute("UPDATE league_predictions SET prediction_status='locked'")
        self.assertEqual(self.run_pub()['status'], 'sent')

    def test_stale_data_waits_even_with_official_prediction(self):
        self.fixture()
        with self.db.connect() as c: c.execute('UPDATE agent_runs SET completed_at=?', ((MORNING-timedelta(hours=2)).isoformat(),))
        self.assertEqual(self.run_pub()['status'], 'waiting')
        self.send.assert_not_called()

    def test_stale_fixture_rows_wait(self):
        self.fixture()
        with self.db.connect() as c: c.execute('UPDATE league_matches SET updated_at=?', ((MORNING-timedelta(hours=2)).isoformat(),))
        self.assertEqual(self.run_pub()['status'], 'waiting')

    def test_missing_refresh_marker_waits_without_assuming_workflow_ran(self):
        self.fixture()
        with self.db.connect() as c: c.execute("DELETE FROM agent_runs WHERE command='league-fixtures-refresh:PL'")
        self.assertEqual(self.run_pub()['status'], 'waiting')

    def test_never_sends_after_kickoff_or_morning_window(self):
        self.fixture(kickoff='2026-10-10T07:30:00Z')
        self.assertEqual(self.run_pub()['status'], 'expired')
        self.send.assert_not_called()
        self.assertFalse(json.loads(self.records()[0]['details'])['retryable'])
        with self.assertRaises(ValueError): self.run_pub(recover=True)

    def test_before_window_and_after_window(self):
        self.fixture()
        self.assertEqual(self.run_pub(now=MORNING-timedelta(minutes=1))['status'], 'outside-window')
        self.refresh(MORNING+timedelta(hours=3))
        self.assertEqual(self.run_pub(now=MORNING+timedelta(hours=3))['status'], 'expired')
        self.send.assert_not_called()

    def test_no_retrospective_official_lock(self):
        self.fixture()
        with self.db.connect() as c: c.execute("UPDATE league_predictions SET locked_at='2026-10-10T15:00:00Z'")
        self.assertEqual(self.run_pub()['status'], 'waiting')

    def test_results_final_scores_original_pick_and_daily_accuracy(self):
        self.fixture('1', lifecycle='evaluated', status='completed', home_score=2, away_score=1)
        self.fixture('2', status='completed', home_score=0, away_score=0)
        self.fixture('3', lifecycle='provisional', status='completed', home_score=3, away_score=0)
        self.fixture('4', lifecycle=None, status='completed', home_score=0, away_score=1)
        self.refresh(EVENING)
        before = self.db.rows('SELECT * FROM league_predictions')
        self.assertEqual(self.run_pub('RESULTS')['status'], 'sent')
        message = self.send.call_args.args[2]
        for expected in ['Arsenal 1 2–1 Chelsea', 'Original Official Pick: Arsenal 1', '✅ Correct', '❌ Incorrect',
                         'Daily official picks: 1/2 · Accuracy: 50.0%', 'Provisional Pick · Not scored', 'Official prediction unavailable · Not scored']:
            self.assertIn(expected, message)
        self.assertEqual(before, self.db.rows('SELECT * FROM league_predictions'))

    def test_unfinished_or_unconfirmed_results_wait(self):
        for status, hs, aws in [('live', 1, 0), ('scheduled', None, None), ('completed', 1, None)]:
            self.fixture(status, status=status, home_score=hs, away_score=aws)
        self.refresh(EVENING)
        self.assertEqual(self.run_pub('RESULTS')['status'], 'waiting')
        self.send.assert_not_called()

    def test_postponed_cancelled_explicit_and_not_scored(self):
        self.fixture('1', status='postponed')
        self.fixture('2', status='cancelled')
        self.run_pub()
        self.assertIn('Postponed', self.send.call_args.args[2])
        self.refresh(EVENING)
        self.run_pub('RESULTS')
        message = self.send.call_args.args[2]
        self.assertIn('Postponed · Not scored', message)
        self.assertIn('Cancelled · Not scored', message)
        self.assertIn('Accuracy: N/A', message)

    def test_rescheduled_fixture_preserves_original_morning_identity(self):
        self.fixture()
        self.run_pub()
        with self.db.connect() as c: c.execute("UPDATE league_matches SET kickoff='2026-10-17T14:00:00Z'")
        self.refresh(EVENING)
        self.run_pub('RESULTS')
        self.assertIn('Postponed · Not scored', self.send.call_args.args[2])

    def test_delayed_results_after_midnight_keep_match_day_identity(self):
        self.fixture(status='live')
        self.refresh(EVENING)
        self.assertEqual(self.run_pub('RESULTS')['status'], 'waiting')
        late = datetime(2026, 10, 11, 0, 15, tzinfo=timezone.utc)
        with self.db.connect() as c: c.execute("UPDATE league_matches SET status='completed',home_score=2,away_score=0")
        self.refresh(late)
        outcome = self.run_pub('RESULTS', now=late)
        self.assertEqual(outcome['identity'], 'PL:2026-10-10:RESULTS')
        self.assertEqual(outcome['status'], 'sent')

    def test_retry_expiry_is_visible_and_manual_recovery_requires_confirmed_data(self):
        self.fixture(status='live')
        expired = datetime(2026, 10, 11, 11, tzinfo=timezone.utc)  # noon BST
        self.refresh(expired)
        self.assertEqual(self.run_pub('RESULTS', now=expired)['status'], 'expired')
        self.assertEqual(self.records()[0]['status'], 'failed')
        self.assertEqual(self.run_pub('RESULTS', now=expired, recover=True, target_date='2026-10-10')['status'], 'waiting')
        with self.db.connect() as c: c.execute("UPDATE league_matches SET status='completed',home_score=0,away_score=1")
        self.assertEqual(self.run_pub('RESULTS', now=expired, recover=True, target_date='2026-10-10')['status'], 'sent')

    def test_bst_gmt_transitions_windows_and_midnight(self):
        for day, hour in [('2026-03-29', 8), ('2026-10-25', 9)]:
            start, end = window(datetime.fromisoformat(day).date(), 'MORNING')
            self.assertEqual(start.astimezone(timezone.utc).hour, hour)
            self.assertEqual(end.hour, 12)
        for instant in ['2026-03-29T00:15:00Z', '2026-10-25T00:15:00Z', '2026-10-25T01:15:00Z']:
            local = datetime.fromisoformat(instant).astimezone(LONDON)
            self.assertEqual(result_day(local), local.date()-timedelta(days=1))

    def test_duplicate_and_concurrent_invocations(self):
        self.fixture()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.run_pub()['status'], range(2)))
        self.assertEqual(sorted(results), ['already-sent', 'sent'])
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.run_pub()['status'], 'already-sent')

    def test_failed_send_retry_and_persisted_message_ids(self):
        self.fixture()
        self.send.side_effect = TelegramError('network failure')
        with self.assertRaises(TelegramError): self.run_pub()
        self.assertEqual(self.records()[0]['status'], 'failed')
        self.send.side_effect = None
        self.assertEqual(self.run_pub()['status'], 'sent')
        self.assertEqual(json.loads(self.records()[0]['details'])['delivered'], [123])

    def test_lost_confirmation_remains_retryable_residual_duplicate_risk(self):
        self.fixture()
        self.send.side_effect = [TelegramError('confirmation lost'), {'result': {'message_id': 123}}]
        with self.assertRaises(TelegramError): self.run_pub()
        self.assertEqual(self.run_pub()['status'], 'sent')
        self.assertEqual(self.send.call_count, 2)  # Telegram may have accepted both.

    def test_split_and_partial_checkpoint_retry(self):
        for mid in range(35): self.fixture(str(mid))
        first = self.run_pub(max_parts=1)
        self.assertEqual(first['status'], 'partial')
        first_message = self.send.call_args.args[2]
        self.send.reset_mock()
        self.send.side_effect = TelegramError('failed')
        with self.assertRaises(TelegramError): self.run_pub(max_parts=1)
        failed_message = self.send.call_args.args[2]
        self.send.reset_mock()
        self.send.side_effect = None
        self.assertEqual(self.run_pub()['status'], 'sent')
        self.assertEqual(self.send.call_args_list[0].args[2], failed_message)
        self.assertNotEqual(first_message, failed_message)
        payload = json.loads(self.records()[0]['details'])
        self.assertTrue(all(units(message) <= 3900 for message in payload['messages']))
        for mid in range(35):
            self.assertEqual(sum(f'Arsenal {mid} vs Chelsea' in message for message in payload['messages']), 1)

    def test_changed_partial_plan_and_kickoff_block_retry(self):
        for mid in range(35): self.fixture(str(mid))
        self.run_pub(max_parts=1)
        with self.db.connect() as c: c.execute("UPDATE league_matches SET home_team='Changed' WHERE id='34'")
        self.assertEqual(self.run_pub()['status'], 'plan-changed')
        self.assertEqual(self.send.call_count, 1)

    def test_missing_credentials_fail_with_clear_retryable_status(self):
        self.fixture()
        for name in ['telegram_token', 'telegram_chat_id']:
            settings = SimpleNamespace(telegram_token='token', telegram_chat_id='chat', request_timeout=15)
            setattr(settings, name, None)
            with patch('src.league_publications.get_settings', return_value=settings):
                with self.assertRaises(TelegramError): self.run_pub()
            self.assertEqual(self.records()[0]['status'], 'failed')
        self.send.assert_not_called()

    def test_both_dry_runs_are_byte_for_byte_read_only_and_no_api_calls(self):
        self.fixture()
        self.refresh(EVENING)
        before = self.path.read_bytes()
        db = Database(self.path, read_only=True, initialize_schema=False)
        with redirect_stdout(io.StringIO()), patch('src.league_service.daily_league') as refresh:
            for publication in ['MORNING', 'RESULTS']:
                outcome = publish(db, publication=publication, dry_run=True, now=EVENING, target_date='2026-10-10')
                self.assertEqual(outcome['status'], 'dry-run')
                self.assertTrue(outcome['messages'])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(self.records(), [])
        refresh.assert_not_called()
        self.send.assert_not_called()

    def test_dry_run_of_unsent_retry_reflects_latest_persisted_data(self):
        self.fixture()
        self.send.side_effect = TelegramError('failed')
        with self.assertRaises(TelegramError): self.run_pub()
        with self.db.connect() as c: c.execute("UPDATE league_matches SET home_team='Updated Arsenal'")
        with redirect_stdout(io.StringIO()) as output:
            self.run_pub(dry_run=True)
        self.assertIn('Updated Arsenal vs Chelsea', output.getvalue())
        self.send.side_effect = None
        self.run_pub()
        self.assertEqual(output.getvalue(), self.send.call_args.args[2] + '\n\n')

    def test_http_tick_budget_sends_only_one_part_without_refresh(self):
        for mid in range(35): self.fixture(str(mid))
        with patch('src.league_service.daily_league') as refresh:
            outcomes = tick(self.db, now=MORNING)
        self.assertEqual(sum(o['sent_parts'] for o in outcomes), 1)
        self.assertEqual(self.send.call_count, 1)
        refresh.assert_not_called()

    def test_tick_defers_when_request_processing_budget_is_exhausted(self):
        with patch('src.league_publications.runtime_time.monotonic', side_effect=[0, 31]):
            self.assertEqual(tick(self.db, now=MORNING), [])
        self.send.assert_not_called()

    def test_partial_morning_retry_cannot_cross_kickoff(self):
        for mid in range(35): self.fixture(str(mid), kickoff='2026-10-10T08:01:00Z')
        self.run_pub(max_parts=1)
        self.assertEqual(self.run_pub(now=MORNING+timedelta(minutes=1))['status'], 'expired')
        self.assertEqual(self.send.call_count, 1)

    def test_results_splitting_keeps_scores_and_daily_total(self):
        for mid in range(55): self.fixture(str(mid), status='completed', home_score=2, away_score=1)
        self.refresh(EVENING)
        self.run_pub('RESULTS')
        messages = [call.args[2] for call in self.send.call_args_list]
        self.assertGreater(len(messages), 1)
        self.assertTrue(all(units(message) <= 3900 for message in messages))
        self.assertTrue(all('55/55 · Accuracy: 100.0%' in message for message in messages))
        for mid in range(55):
            self.assertEqual(sum(f'Arsenal {mid} 2–1 Chelsea' in message for message in messages), 1)

    def test_requires_explicit_migration_no_live_schema_creation(self):
        self.fixture()
        with self.db.connect() as c: c.execute('DROP INDEX idx_telegram_publication_identity')
        with self.assertRaisesRegex(ValueError, 'Apply sql'): self.run_pub()
        self.send.assert_not_called()
        self.assertEqual(self.db.rows("SELECT * FROM sqlite_master WHERE name='idx_telegram_publication_identity'"), [])

    def test_migration_idempotent_preserves_v1_delivery(self):
        self.fixture()
        with self.db.connect() as c:
            c.execute("INSERT INTO agent_runs(started_at,command,status,details) VALUES('now','league-telegram:PL:2026-10-10','success','{}')")
            c.executescript(MIGRATION.read_text())
            c.executescript(MIGRATION.read_text())
        self.assertEqual(self.run_pub()['status'], 'already-sent')
        self.send.assert_not_called()

    def test_refresh_success_failure_and_history_tracking(self):
        from src.league_service import fetch_league
        self.fixture()
        match = self.db.league_matches('PL')[0]
        settings = SimpleNamespace(api_key='mock-api-key', api_url=None, request_timeout=15)
        with patch('src.league_service.get_settings', return_value=settings), patch('src.league_service.LeagueAPIClient') as api:
            api.return_value.matches.return_value = [match]
            api.return_value.standings.return_value = []
            fetch_league(self.db, 'PL')
            count = len(self.db.rows("SELECT * FROM agent_runs WHERE command='league-fixtures-refresh:PL'"))
            fetch_league(self.db, 'PL', season=2025)
            self.assertEqual(len(self.db.rows("SELECT * FROM agent_runs WHERE command='league-fixtures-refresh:PL'")), count)
            api.return_value.matches.side_effect = RuntimeError('api-secret')
            with self.assertRaises(RuntimeError): fetch_league(self.db, 'PL')
        rows = self.db.rows("SELECT * FROM agent_runs WHERE command='league-fixtures-refresh:PL' ORDER BY id")
        self.assertEqual(rows[-2]['status'], 'success')
        self.assertEqual(rows[-1]['status'], 'failed')
        self.assertNotIn('api-secret', rows[-1]['details'])


class HTTPWorkerTests(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app, base_url='https://worker.example')
        self.env = patch.dict(os.environ, {'TELEGRAM_PUBLISHER_SECRET': 'x'*32, 'TELEGRAM_PUBLISHER_ENABLED': 'true'})
        self.env.start()
        self.db_patch = patch('src.telegram_worker.Database')
        self.db = self.db_patch.start()
        self.tick_patch = patch('src.telegram_worker.tick', return_value=[{'status': 'waiting'}])
        self.tick = self.tick_patch.start()
        self.settings_patch = patch('src.telegram_worker.get_settings', return_value=SimpleNamespace(database_path=Path('unused'), database_url='postgresql://mock'))
        self.settings_patch.start()

    def tearDown(self):
        self.settings_patch.stop()
        self.tick_patch.stop()
        self.db_patch.stop()
        self.env.stop()

    def post(self, token='x'*32, **body):
        return self.client.post('/internal/telegram/check', headers={'Authorization': f'Bearer {token}'}, json=body)

    def test_missing_invalid_auth_never_touches_db_or_worker(self):
        self.assertEqual(self.client.post('/internal/telegram/check', json={}).status_code, 401)
        self.assertEqual(self.post('invalid').status_code, 401)
        self.db.assert_not_called()
        self.tick.assert_not_called()

    def test_missing_secret_disabled_or_missing_database_fail_closed(self):
        for name, value in [('TELEGRAM_PUBLISHER_SECRET', ''), ('TELEGRAM_PUBLISHER_ENABLED', 'false')]:
            with patch.dict(os.environ, {name: value}): self.assertEqual(self.post().status_code, 503)
        with patch('src.telegram_worker.get_settings', return_value=SimpleNamespace(database_url=None)):
            self.assertEqual(self.post().status_code, 503)
        self.tick.assert_not_called()

    def test_authenticated_dry_run_is_read_only(self):
        self.assertEqual(self.post(dry_run=True).status_code, 200)
        self.db.assert_called_once_with(Path('unused'), 'postgresql://mock', initialize_schema=False, read_only=True, bounded_worker=True)
        self.tick.assert_called_once_with(self.db.return_value, dry_run=True)

    def test_disabled_http_blocks_live_but_allows_authenticated_preview(self):
        with patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED='false'):
            self.assertEqual(self.post().status_code, 503)
            self.db.assert_not_called()
            self.tick.assert_not_called()
            self.assertEqual(self.post(dry_run=True).status_code, 200)
            self.tick.assert_called_once_with(self.db.return_value, dry_run=True)

    def test_authenticated_live_tick_and_errors_are_sanitized(self):
        self.assertEqual(self.post().status_code, 200)
        for error, status in [(TelegramError('token-secret'), 502), (RuntimeError('db-secret'), 503)]:
            self.tick.side_effect = error
            response = self.post()
            self.assertEqual(response.status_code, status)
            self.assertNotIn('secret', response.text)

    def test_recovery_date_override_cannot_be_sent_through_http(self):
        self.assertEqual(self.post(recover=True, date='2026-10-10').status_code, 422)
        self.tick.assert_not_called()

    def test_plain_http_is_rejected(self):
        with patch.dict(os.environ, {'VERCEL': ''}):
            response = self.client.post('http://worker.example/internal/telegram/check', json={}, headers={'Authorization': 'Bearer ' + 'x'*32})
        self.assertEqual(response.status_code, 403)
        self.tick.assert_not_called()


class ConfigurationTests(unittest.TestCase):
    def test_vercel_entrypoint_registers_internal_worker(self):
        import app as entrypoint
        routes = [route for route in entrypoint.app.routes if getattr(route, 'path', '') == '/internal/telegram/check']
        self.assertEqual(len(routes), 1)
        self.assertEqual(routes[0].methods, {'POST'})
        self.assertFalse(routes[0].include_in_schema)
        config = json.loads((ROOT/'vercel.json').read_text())
        self.assertIn('app.py', config['functions'])

    def test_cron_sql_is_paused_and_uses_vault_no_literal_credentials(self):
        sql = (ROOT/'sql'/'supabase_telegram_cron.sql').read_text()
        self.assertIn("'*/15 * * * *'", sql)
        self.assertIn('vault.decrypted_secrets', sql)
        self.assertIn('SET active = false', sql)
        self.assertIn('REVOKE ALL ON net.http_request_queue', sql)
        self.assertNotIn('vault.create_secret(', sql)

    def test_only_telegram_github_schedule_removed(self):
        workflow = (ROOT/'.github/workflows/league_telegram.yml').read_text()
        self.assertNotIn('schedule:', workflow)
        self.assertNotIn('league-daily', workflow)
        self.assertIn('workflow_dispatch:', workflow)
        self.assertIn('schedule:', (ROOT/'.github/workflows/sportsintel-league.yml').read_text())


if __name__ == '__main__':
    unittest.main()
