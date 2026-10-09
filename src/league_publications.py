"""Bounded persisted-data publisher: no ingestion, ML, schema DDL, or timers."""
from __future__ import annotations

import json
import logging
import time as runtime_time
from datetime import date, datetime, time, timedelta, timezone

from .config import get_settings
from .database import Database, parse_utc
from .league_config import get_league
from .league_telegram import FOOTER, LONDON, MAX_UNITS, _lock, build_messages, clean, units, valid_prediction
from .telegram_bot import TelegramError, publishing_enabled, send_message

LOGGER = logging.getLogger(__name__)
FRESHNESS = timedelta(minutes=90)
SEND_TIMEOUT = 10


def local_now(now=None):
    return (now or datetime.now(timezone.utc)).astimezone(LONDON)


def result_day(local: datetime) -> date:
    return local.date() if local.time() >= time(23, 30) else local.date() - timedelta(days=1)


def window(day: date, publication: str):
    if publication == 'MORNING':
        return datetime.combine(day, time(9), LONDON), datetime.combine(day, time(12), LONDON)
    return datetime.combine(day, time(23, 30), LONDON), datetime.combine(day + timedelta(days=1), time(12), LONDON)


def fresh(value, now):
    if not value:
        return False
    try:
        age = now - parse_utc(value)
        return -timedelta(minutes=5) <= age <= FRESHNESS
    except (ValueError, TypeError):
        return False


def official(match):
    if not valid_prediction(match) or match.get('prediction_status') not in {'locked', 'evaluated'}:
        return False
    try:
        return bool(match.get('locked_at')) and parse_utc(match['locked_at']) < parse_utc(match['kickoff'])
    except (ValueError, TypeError):
        return False


def snapshot(db, code, day, connection=None, publication='MORNING'):
    # Reuse the same persisted query as the website; London calendar identity.
    original_ids = set()
    if publication == 'RESULTS':
        morning = db.rows('SELECT details FROM agent_runs WHERE command=?', (f'{code}:{day.isoformat()}:MORNING',), connection=connection)
        if morning:
            original_ids = set(json.loads(morning[0]['details'] or '{}').get('fixture_ids', []))
    matches = []
    published_ids = set()
    if publication == 'RESULTS':
        for row in db.rows("SELECT command,details FROM agent_runs WHERE command LIKE ?", (f'{code}:%:RESULTS',), connection=connection):
            if row['command'] == f'{code}:{day.isoformat()}:RESULTS':
                continue
            payload = json.loads(row['details'] or '{}')
            for ids in payload.get('result_fixture_ids_by_part', [])[:len(payload.get('delivered', []))]:
                published_ids.update(ids)
    for match in db.league_matches(code, connection=connection):
        kickoff_day = parse_utc(match['kickoff']).astimezone(LONDON).date() if match.get('kickoff') else None
        if kickoff_day == day or match['id'] in original_ids:
            if kickoff_day != day:
                # Morning history supplies notices only, never ownership of a score.
                notice = match['status'].title() if match['status'] in {'postponed', 'cancelled'} else 'Postponed' if match['status'] == 'scheduled' else 'Rescheduled'
                match = {**match, 'result_notice': notice}
            elif publication == 'RESULTS' and match['id'] in published_ids:
                match = {**match, 'result_notice': 'Result already published'}
            matches.append(match)
    return sorted(matches, key=lambda m: (parse_utc(m['kickoff']) if m.get('kickoff') else datetime.max.replace(tzinfo=timezone.utc), m['id']))


def readiness(db, matches, code, publication, now, connection=None):
    refresh = db.rows("SELECT completed_at FROM agent_runs WHERE command=? AND status='success' ORDER BY completed_at DESC LIMIT 1",
                      (f'league-fixtures-refresh:{code}',), connection=connection)
    if not refresh or not fresh(refresh[0]['completed_at'], now):
        return 'Current fixture refresh is missing or older than 90 minutes'
    if any(not fresh(m.get('updated_at'), now) for m in matches):
        return 'Fixture/results data is older than 90 minutes'
    for match in matches:
        if match.get('result_notice') or match['status'] in {'postponed', 'cancelled'}:
            continue
        if publication == 'MORNING':
            if match['status'] != 'scheduled' or parse_utc(match['kickoff']) <= now:
                return 'A relevant fixture has already kicked off'
            if not official(match):
                return 'Waiting for valid pre-kickoff official predictions'
        elif match['status'] != 'completed' or not all(isinstance(match.get(key), int) and match[key] >= 0 for key in ('home_score', 'away_score')):
            return 'Waiting for confirmed final scores'
    return None


def split_blocks(header, continuation, blocks, footer=FOOTER, *, fixture_ids=None, part_ids=None):
    messages, current = [], header
    current_ids = []
    for index, block in enumerate(blocks):
        if units(continuation + '\n\n' + block + '\n\n' + footer) > MAX_UNITS:
            raise ValueError('A fixture is too large for Telegram')
        if units(current + '\n\n' + block + '\n\n' + footer) > MAX_UNITS:
            messages.append(current + '\n\n' + footer)
            if part_ids is not None:
                part_ids.append(current_ids)
            current_ids = []
            current = continuation
        current += '\n\n' + block
        if fixture_ids is not None and fixture_ids[index] is not None:
            current_ids.append(fixture_ids[index])
    if blocks and part_ids is not None:
        part_ids.append(current_ids)
    return messages + [current + '\n\n' + footer] if blocks else []


def messages_for(matches, league, day, publication, part_ids=None):
    if publication == 'MORNING':
        active = [m for m in matches if m['status'] not in {'postponed', 'cancelled'}]
        messages = build_messages(active, league.name, day)
        excluded = [f"{clean(m['home_team'])} vs {clean(m['away_team'])}: {m['status'].title()}" for m in matches if m['status'] in {'postponed', 'cancelled'}]
        if excluded:
            messages += split_blocks(f"SportsIntelAI · {league.name} · {day.isoformat()}\nFixture updates", 'SportsIntelAI · Fixture updates (continued)', excluded)
        return messages
    blocks, correct, total = [], 0, 0
    fixture_ids = []
    for match in matches:
        fixture_ids.append(None)
        name = f"{clean(match['home_team'])} vs {clean(match['away_team'])}"
        if match.get('result_notice'):
            blocks.append(f"{name}\n{match['result_notice']} · Not scored")
            continue
        if match['status'] in {'postponed', 'cancelled'}:
            blocks.append(f"{name}\n{match['status'].title()} · Not scored")
            continue
        if match['status'] != 'completed' or match.get('home_score') is None or match.get('away_score') is None:
            blocks.append(f"{name}\nResult awaiting confirmation · Not scored")
            continue
        home, away = match['home_score'], match['away_score']
        fixture_ids[-1] = match['id']
        block = f"{clean(match['home_team'])} {home}–{away} {clean(match['away_team'])}"
        if official(match):
            actual = 'H' if home > away else 'A' if away > home else 'D'
            hit = match['predicted_outcome'] == actual
            pick = {'H': match['home_team'], 'D': 'Draw', 'A': match['away_team']}[match['predicted_outcome']]
            block += f"\nOriginal Official Pick: {clean(pick)}\n{'✅ Correct' if hit else '❌ Incorrect'}"
            total += 1
            correct += hit
        else:
            label = 'Provisional Pick' if valid_prediction(match) and match.get('prediction_status') == 'provisional' else 'Official prediction unavailable'
            block += f"\n{label} · Not scored"
        blocks.append(block)
    accuracy = f'{correct / total:.1%}' if total else 'N/A'
    footer = f'Daily official picks: {correct}/{total} · Accuracy: {accuracy}\n\n{FOOTER}'
    return split_blocks(f"⚽ SPORTSINTELAI — MATCH-DAY RESULTS\n\n{league.name} · {day.strftime('%d %B %Y')}",
                        f'SportsIntelAI · Results · {day.isoformat()} (continued)', blocks, footer,
                        fixture_ids=fixture_ids, part_ids=part_ids)


def _row(connection, identity):
    row = connection.execute('SELECT * FROM agent_runs WHERE command=?', (identity,)).fetchone()
    return dict(row) if row else None


def _state(connection, row, identity, status, payload, now):
    timestamp = now.astimezone(timezone.utc).isoformat(timespec='seconds')
    done = timestamp if status in {'success', 'failed'} else None
    details = json.dumps(payload)
    if row:
        connection.execute('UPDATE agent_runs SET status=?,completed_at=?,details=? WHERE id=?', (status, done, details, row['id']))
    else:
        connection.execute('INSERT INTO agent_runs(started_at,completed_at,command,status,details) VALUES(?,?,?,?,?)', (timestamp, done, identity, status, details))


def ensure_migrated(db, connection=None):
    # Verify only: migrations must be reviewed/applied explicitly by an operator.
    if db.backend == 'postgres':
        indexes = db.rows("SELECT indexname FROM pg_indexes WHERE schemaname='public' AND tablename='agent_runs' AND indexname='idx_telegram_publication_identity'", connection=connection)
    else:
        indexes = db.rows("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_telegram_publication_identity'", connection=connection)
    if not indexes:
        raise ValueError('Apply sql/telegram_publications_v2.sql before live publication')


def publish(db: Database, code='PL', publication='MORNING', *, dry_run=False,
            now=None, target_date=None, recover=False, max_parts=None, deadline=None):
    league = get_league(code)
    publication = publication.upper()
    if publication not in {'MORNING', 'RESULTS'}:
        raise ValueError('Publication must be MORNING or RESULTS')
    if recover and publication != 'RESULTS':
        raise ValueError('Recovery is permitted only for RESULTS; morning kickoff protection cannot be bypassed')
    current = local_now(now)
    day = date.fromisoformat(target_date) if isinstance(target_date, str) else target_date
    day = day or (current.date() if publication == 'MORNING' else result_day(current))
    identity = f'{league.code}:{day.isoformat()}:{publication}'
    if not dry_run and not publishing_enabled():
        return {'identity': identity, 'status': 'disabled', 'sent_parts': 0}
    start, end = window(day, publication)
    LOGGER.info('[telegram-agent] Publication: %s; UK time: %s', identity, current.isoformat())
    if not dry_run and current < start:
        return {'identity': identity, 'status': 'outside-window', 'sent_parts': 0}
    if dry_run:
        with db.connect() as connection:
            previous = _row(connection, identity)
            matches = snapshot(db, league.code, day, connection, publication)
            issue = readiness(db, matches, league.code, publication, current, connection)
            messages = messages_for(matches, league, day, publication)
            saved = json.loads(previous['details'] or '{}') if previous else {}
            if saved.get('delivered'):
                if saved.get('messages') != messages:
                    issue = 'Persisted plan changed after partial delivery; operator review required'
                messages = saved['messages']
        for message in messages:
            print(message + "\n")
        LOGGER.info('[telegram-agent] Dry run readiness: %s', issue or 'ready')
        return {'identity': identity, 'status': 'dry-run', 'sent_parts': 0, 'messages': messages, 'readiness': issue}
    sent = 0
    while True:
        if not publishing_enabled():
            return {'identity': identity, 'status': 'disabled', 'sent_parts': sent}
        if deadline is not None and runtime_time.monotonic() >= deadline:
            return {'identity': identity, 'status': 'deferred', 'sent_parts': sent}
        failure = None
        with db.connect() as connection:
            _lock(connection, db, f'{league.code}:RESULTS' if publication == 'RESULTS' else identity)
            ensure_migrated(db, connection)
            row = _row(connection, identity)
            payload = json.loads(row['details'] or '{}') if row else {}
            if row and row['status'] == 'success':
                return {'identity': identity, 'status': 'already-sent', 'sent_parts': sent}
            if row and row['status'] == 'failed' and payload.get('retryable') is False and not recover:
                return {'identity': identity, 'status': 'expired' if 'expired' in payload.get('reason', '') or 'kicked off' in payload.get('reason', '') else 'plan-changed', 'sent_parts': sent, 'reason': payload.get('reason')}
            current = local_now(now)
            # Recheck windows and kickoff before EVERY part, including retries.
            matches = snapshot(db, league.code, day, connection, publication)
            issue = readiness(db, matches, league.code, publication, current, connection)
            if not matches and not issue:
                LOGGER.info('No Premier League matches on %s. Nothing sent.', day)
                return {'identity': identity, 'status': 'no-matches', 'sent_parts': sent}
            past_kickoff = publication == 'MORNING' and any(m['status'] not in {'postponed', 'cancelled'} and parse_utc(m['kickoff']) <= current for m in matches)
            expired = current >= end and not recover
            if expired or past_kickoff:
                payload.update(reason='Publication retry window expired' if expired else 'Relevant match has kicked off', retryable=False)
                _state(connection, row, identity, 'failed', payload, current)
                LOGGER.error('[telegram-agent] %s failed: %s', identity, payload['reason'])
                return {'identity': identity, 'status': 'expired', 'sent_parts': sent, 'reason': payload['reason']}
            if issue:
                payload.update(reason=issue, retryable=True)
                _state(connection, row, identity, 'waiting', payload, current)
                LOGGER.warning('[telegram-agent] %s waiting: %s', identity, issue)
                return {'identity': identity, 'status': 'waiting', 'sent_parts': sent, 'reason': issue}
            if not matches:
                return {'identity': identity, 'status': 'no-matches', 'sent_parts': sent}
            part_ids = []
            latest_messages = messages_for(matches, league, day, publication, part_ids)
            if payload.get('delivered') and payload.get('messages') != latest_messages:
                payload.update(reason='Persisted fixture/prediction plan changed after partial delivery; operator review required', retryable=False)
                _state(connection, row, identity, 'failed', payload, current)
                return {'identity': identity, 'status': 'plan-changed', 'sent_parts': sent}
            if deadline is not None and runtime_time.monotonic() >= deadline:
                return {'identity': identity, 'status': 'deferred', 'sent_parts': sent}
            # Include network timeout as a safety margin: do not START a part
            # that could reach a morning kickoff or publication-window boundary.
            current = local_now(now)
            margin = current + timedelta(seconds=SEND_TIMEOUT)
            if publication == 'MORNING' and any(m['status'] not in {'postponed', 'cancelled'} and parse_utc(m['kickoff']) <= margin for m in matches):
                payload.update(reason='Relevant match has kicked off or is inside the delivery safety margin', retryable=False)
                _state(connection, row, identity, 'failed', payload, current)
                return {'identity': identity, 'status': 'expired', 'sent_parts': sent}
            if not recover and margin >= end:
                return {'identity': identity, 'status': 'deferred', 'sent_parts': sent}
            settings = get_settings()
            if not publishing_enabled():
                return {'identity': identity, 'status': 'disabled', 'sent_parts': sent}
            if not settings.telegram_token or not settings.telegram_chat_id:
                payload.update(reason='Missing Telegram credentials', retryable=True)
                _state(connection, row, identity, 'failed', payload, current)
                failure = TelegramError('TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required')
            elif payload.get('chat_id') and payload['chat_id'] != settings.telegram_chat_id:
                payload.update(reason='Telegram destination changed; operator review required', retryable=False)
                _state(connection, row, identity, 'failed', payload, current)
                failure = TelegramError(payload['reason'])
            else:
                if not payload.get('delivered'):
                    payload.update(messages=latest_messages, delivered=[], chat_id=settings.telegram_chat_id,
                                   fixture_ids=[m['id'] for m in matches])
                if publication == 'RESULTS':
                    payload['result_fixture_ids_by_part'] = part_ids
                part = len(payload['delivered'])
                try:
                    result = send_message(settings.telegram_token, settings.telegram_chat_id, payload['messages'][part], min(SEND_TIMEOUT, settings.request_timeout))
                except TelegramError:
                    payload.update(reason='Telegram delivery failed or confirmation lost', retryable=True)
                    _state(connection, row, identity, 'failed', payload, current)
                    failure = TelegramError(payload['reason'])
                else:
                    payload['delivered'].append(result['result']['message_id'])
                    payload.update(reason=None, retryable=True)
                    done = len(payload['delivered']) == len(payload['messages'])
                    _state(connection, row, identity, 'success' if done else 'running', payload, current)
                    sent += 1
        if failure:
            raise failure
        if done or (max_parts is not None and sent >= max_parts):
            status = 'sent' if done else 'partial'
            LOGGER.info('[telegram-agent] %s: %s; confirmed parts this invocation: %d', identity, status, sent)
            return {'identity': identity, 'status': status, 'sent_parts': sent}


def tick(db, *, dry_run=False, now=None):
    """At most ONE Telegram request per HTTP invocation (<60s host cap)."""
    current = local_now(now)
    candidates = [('RESULTS', result_day(current))]
    if current.hour >= 9:
        candidates.append(('MORNING', current.date()))
    outcomes = []
    deadline = runtime_time.monotonic() + 30
    for publication, day in candidates:
        if runtime_time.monotonic() >= deadline:
            break
        outcome = publish(db, publication=publication, target_date=day, dry_run=dry_run, now=now, max_parts=1, deadline=deadline)
        outcomes.append(outcome)
        if outcome.get('sent_parts'):
            break
    return outcomes
