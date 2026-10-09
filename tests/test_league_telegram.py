from __future__ import annotations

import io
import os
import tempfile
import unittest
from http.client import IncompleteRead
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from src.database import Database
from src.league_config import get_league
from src.league_telegram import _lock, build_messages, run_match_day, units
from src.telegram_bot import TelegramError, send_message

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 10, 8, 7, tzinfo=timezone.utc)


class TelegramClientTests(unittest.TestCase):
    def setUp(self):
        enabled = patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED='true')
        enabled.start()
        self.addCleanup(enabled.stop)

    def test_fifa_stays_disabled_independently_of_sportsintel_switch(self):
        import main
        db = Mock()
        db.latest_snapshots.return_value = [{'bracket': []}]
        settings = SimpleNamespace(telegram_token='test', telegram_chat_id='test', request_timeout=10)
        for sportsintel in ['true', 'false']:
            for fifa in ['false', '', 'invalid', None]:
                with patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED=sportsintel), patch('src.telegram_bot.urlopen') as api:
                    if fifa is None: os.environ.pop('FIFA_TELEGRAM_ENABLED', None)
                    else: os.environ['FIFA_TELEGRAM_ENABLED'] = fifa
                    with self.assertRaisesRegex(TelegramError, 'disabled'):
                        send_message('test', 'test', 'preview', publisher='fifa')
                    with patch('main.get_settings', return_value=settings), patch('main.build_message', return_value='preview'), redirect_stdout(io.StringIO()) as output:
                        main.send_latest(db)
                    self.assertEqual(output.getvalue().strip(), 'preview')
                    api.assert_not_called()

    def test_fifa_flag_cannot_enable_sportsintel_transport(self):
        with patch.dict(os.environ, TELEGRAM_PUBLISHER_ENABLED='false', FIFA_TELEGRAM_ENABLED='true'), patch('src.telegram_bot.urlopen') as api:
            with self.assertRaisesRegex(TelegramError, 'disabled'):
                send_message('test', 'test', 'preview')
            api.assert_not_called()

    def test_fifa_daily_still_refreshes_and_rebuilds_when_fifa_publishing_disabled(self):
        import main
        settings = SimpleNamespace(database_path=Path('unused'), database_url=None,
                                   telegram_token='test', telegram_chat_id='test', request_timeout=10)
        with patch.dict(os.environ, FIFA_TELEGRAM_ENABLED='false', TELEGRAM_PUBLISHER_ENABLED='true'), patch('sys.argv', ['main.py', 'daily']), patch('main.get_settings', return_value=settings), patch('main.Database') as database, patch('main.fetch_data') as fetch, patch('main.rebuild') as rebuild, patch('main.build_message', return_value='preview'), patch('src.telegram_bot.urlopen') as api, redirect_stdout(io.StringIO()):
            database.return_value.latest_snapshots.return_value = [{'bracket': []}]
            self.assertEqual(main.main(), 0)
            fetch.assert_called_once_with(database.return_value)
            rebuild.assert_called_once_with(database.return_value, 'daily')
            api.assert_not_called()

    def test_postgres_uses_transaction_scoped_lock(self):
        connection = Mock()
        _lock(connection, SimpleNamespace(backend='postgres'), 'league-telegram:PL:2026-10-10')
        connection.execute.assert_called_once_with('SELECT pg_advisory_xact_lock(hashtext(?))', ('league-telegram:PL:2026-10-10',))

    def test_cli_failure_and_read_only_preview_configuration(self):
        import main
        for dry_run in [False, True]:
            args = ['main.py', 'league-telegram', 'PL'] + (['--dry-run'] if dry_run else [])
            settings = SimpleNamespace(database_path=Path('unused.db'), database_url=None)
            with patch('sys.argv', args), patch('main.get_settings', return_value=settings), patch('main.Database') as db, patch('src.league_telegram.run_match_day', side_effect=TelegramError('failed')):
                self.assertEqual(main.main(), 1)
                db.assert_called_once_with(Path('unused.db'), None, initialize_schema=False, read_only=dry_run)

    def response(self, payload=b'{"ok":true,"result":{"message_id":123}}', status=200):
        response = Mock(status=status)
        response.read.return_value = payload
        context = Mock()
        context.__enter__ = Mock(return_value=response)
        context.__exit__ = Mock(return_value=False)
        return context

    def test_success_plain_text(self):
        with patch('src.telegram_bot.urlopen', return_value=self.response()) as api:
            self.assertEqual(send_message('secret', 'chat', '<Home> & Away')['result']['message_id'], 123)
        self.assertNotIn(b'parse_mode', api.call_args.args[0].data)

    def test_failure_responses(self):
        for payload, status in [(b'bad json', 200), (b'[]', 200), (b'{"ok":false}', 200),
                                (b'{"ok":true}', 200), (b'{}', 503), (b'\xff', 200)]:
            with patch('src.telegram_bot.urlopen', return_value=self.response(payload, status)):
                with self.assertRaises(TelegramError): send_message('secret', 'chat', 'hello')

    def test_network_errors_do_not_expose_token(self):
        errors = [TimeoutError('secret'), URLError('https://api.telegram.org/botsecret'),
                  IncompleteRead(b'secret'),
                  HTTPError('https://api.telegram.org/botsecret', 401, 'secret', {}, None)]
        for error in errors:
            with patch('src.telegram_bot.urlopen', side_effect=error):
                with self.assertRaises(TelegramError) as caught: send_message('secret', 'chat', 'hello')
                self.assertNotIn('secret', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
