# SportsIntelAI Telegram publishing V2

## Architecture audit and choice

The repository uses Python/FastAPI, the Vercel `app.py` entrypoint, Supabase
Postgres (SQLite locally), an hourly GitHub `league-daily PL` workflow,
`league_matches` / `league_predictions`, a 48-hour official locking lifecycle,
the existing `send_message` Telegram helper, and `agent_runs`. V2 reuses all of
these. It does not add an engine, force-lock picks, evaluate stored predictions,
load model artifacts, train, or ingest fixtures inside the HTTP request.

Chosen flow: **Supabase Cron → pg_net HTTPS POST → existing Vercel FastAPI
`/internal/telegram/check` → persisted Supabase data → Telegram**. Keeping Python
on the existing host avoids porting the publisher to a Supabase Edge Function
or paying for a new always-on worker. Supabase documents Cron/pg_net and Vault
for authenticated scheduled calls: [Supabase scheduling guide](https://supabase.com/docs/guides/functions/schedule-functions).

`vercel.json` explicitly caps the existing Python function at **60 seconds**.
The worker sends at most one Telegram part per HTTP invocation, uses a 10-second
Telegram timeout, 3-second Postgres connection timeout, 5-second statement
timeout, 1-second lock timeout, and a 30-second processing budget before starting
a send. There are no background tasks or work after returning the response.
Further parts resume on subsequent checks. Vercel's plan/runtime limits and
Fluid Compute settings must be verified in the actual project; this workspace
cannot determine the deployed plan. The local cap remains 60 seconds even if a
plan allows more. See [Vercel function limits](https://vercel.com/docs/functions/limitations).

Cron checks every 15 minutes: 96 HTTP invocations/day, approximately 2,880 per
30-day month. Existing Vercel invocation/compute/memory and Supabase database
allowances apply; no separate worker host is introduced. Usage charges,
free-tier pausing, project availability and quotas depend on the accounts.
This is more predictable scheduling infrastructure, not an exactly-once or
guaranteed-delivery service. Verify [Vercel pricing](https://vercel.com/docs/functions/usage-and-pricing)
and [Supabase pricing](https://supabase.com/pricing) before activation.

## Preflight and explicit migration

The operator has confirmed the production publication migration is applied,
its unique index is verified, and `pg_cron`, `pg_net` and Vault are installed.
The four Telegram V2 environment variables are configured in Vercel with
`TELEGRAM_PUBLISHER_ENABLED=false`. These are operator confirmations; this
workspace did not connect to production or independently verify its settings.
Nothing was installed, applied, enabled, deployed, or sent by Codex.

Review and run `sql/supabase_telegram_preflight.sql` with a read-only production
connection or SQL editor. Confirm the extension versions, `net.http_post`
signature, Vault view and Cron table. Enable missing extensions only as a
separate operator action. Do not use the publisher to initialize the database.

Review and apply **`sql/telegram_publications_v2.sql`** explicitly to an already
initialized database before any real V2 post. It adds one partial unique index
on `agent_runs.command` and migrates V1 day identities to MORNING identities,
preserving statuses, message plans and confirmed message IDs. It is idempotent,
transactional, compatible with SQLite/Postgres and changes no prediction data.
If conflicting V1/V2 identities exist, the migration aborts; inspect delivery
history before reconciling them. Apply during cutover with publishers paused.

For a local disposable SQLite database, apply the SQL with a SQLite client or:

```powershell
@'
from pathlib import Path
from src.config import get_settings
from src.database import Database
settings = get_settings()
assert not settings.database_url, 'Use the reviewed Postgres migration procedure instead'
db = Database(settings.database_path, initialize_schema=False)
with db.connect() as connection:
    connection.executescript(Path('sql/telegram_publications_v2.sql').read_text())
'@ | python -
```

The service only verifies the index exists; no live send creates schema.
The normal data/prediction agent continues its existing schema lifecycle.

## Secrets and authenticated worker

Vercel environment:

| Name | Purpose |
|---|---|
| `DATABASE_URL` | Same shared Supabase database used by the website |
| `TELEGRAM_BOT_TOKEN` | Existing bot token |
| `TELEGRAM_CHAT_ID` | Existing destination channel/chat |
| `TELEGRAM_PUBLISHER_SECRET` | Random private bearer secret, at least 32 characters |
| `TELEGRAM_PUBLISHER_ENABLED` | Defaults false; operator sets true only after review |
| `REQUEST_TIMEOUT` | Existing setting; publishing caps it at 10 seconds |

The refresh agent still requires `FOOTBALL_DATA_API_KEY` and its existing model
configuration. The HTTP publisher does not require that key or load the model.

Supabase Vault entries, configured through the Vault UI:

- `sportsintel_telegram_worker_url`: the public HTTPS worker URL ending in
  `/internal/telegram/check`, with no credentials, query string or fragment.
- `sportsintel_telegram_worker_secret`: the same random value as
  `TELEGRAM_PUBLISHER_SECRET`.

Do not put secrets into URLs, cron command text, source files, checked-in SQL,
logs or public dashboards. The endpoint is hidden from the public OpenAPI
schema, accepts only POST, requires HTTPS and constant-time bearer comparison,
and fails closed with absent/invalid auth or missing production database.
Disabled publishing blocks live calls; authenticated dry runs remain available.
Only `dry_run` is accepted in the request body; dates and
recovery overrides cannot be injected through HTTP.

Review **`sql/supabase_telegram_cron.sql`**. It reads credentials from Vault at
runtime, uses a private security-definer enqueue function, restricts execution,
revokes application-role access to the pg_net request/response queues and
creates the named job **paused** inside one transaction. The function lives in
the isolated `sportsintel_scheduler` schema. Queue grant changes apply across
pg_net jobs; check other integrations before applying them. Review inherited role
grants and Vault permissions too: queued HTTP headers contain the bearer token,
so Vault alone is insufficient protection. See [Supabase pg_net access guidance](https://supabase.com/docs/guides/troubleshooting/database-roles-can-read-request-headers-queued-by-pg_net-ad6357).
Never dump queue headers or the decrypted Vault view into logs.

If Vercel deployment protection is enabled, configure suitable authenticated
machine access before enabling Cron; pg_net cannot complete an interactive
login. Keep any additional bypass credential in Vault headers, never the URL.

## Publication rules

All times use `Europe/London` with GMT/BST transitions. Cron itself can remain
UTC because the Python worker computes the local windows and date identity.

**MORNING:** starts 09:00 UK, retries until 12:00 UK, and stops earlier if any
relevant match reaches kickoff. A 10-second delivery safety margin prevents
starting a message immediately before kickoff/window closure. Every scheduled
fixture must have a valid stored official pick locked before kickoff. Missing,
invalid or provisional picks cause a waiting status and retry; dry runs still
show provisional/unavailable labels for inspection. Official probabilities
are never refreshed or modified by publishing. Postponed/cancelled fixtures
are shown explicitly. Morning kickoff protection cannot be bypassed manually.

**RESULTS:** starts 23:30 UK on the fixture date and retries until 12:00 UK the
next day. Before 23:30 a normal results invocation targets yesterday; after
23:30 it targets today. `--date` selects an explicit match day for inspection
or recovery. Unfinished matches or missing final scores cause waiting and no
post. Confirmed completed scores determine the displayed outcome; the original
persisted official pick determines correct/incorrect. This comparison does not
update `actual_outcome`, `correct`, or any lifecycle fields. Provisional,
missing or retrospectively locked predictions are not scored. Daily accuracy
uses only valid official picks on completed games; no eligible picks means
`0/0`, accuracy `N/A`. Postponed/cancelled matches are excluded from that total.

Morning plans retain fixture IDs so results can explicitly report a fixture
rescheduled to a later date or with kickoff removed. V1 plans do not contain
these IDs; legacy rescheduling history may need operator review. Both services
use the dashboard's persisted match/prediction join. London calendar filtering
differs from the dashboard's current UTC date-string filtering near midnight.

**Freshness:** successful current-fixture fetches now record
`league-fixtures-refresh:PL` in `agent_runs`. Historical fetches never satisfy
this signal. A current fetch must return matches to be marked successful.
Before publishing, the most recent success and every selected fixture row must
be no more than 90 minutes old (future clock skew tolerance: five minutes).
This allows the existing hourly refresh cadence without assuming a workflow
ran. A fresh fixture fetch alone does not satisfy morning prediction readiness.
After deployment, run the existing league refresh once to establish this new
marker. GitHub delays/failures can still delay publications; this is visible as
waiting and ultimately expiry rather than stale or invented messages.

## Durable delivery and limits

`agent_runs.command` is the exact identity:
`PL:YYYY-MM-DD:MORNING` or `PL:YYYY-MM-DD:RESULTS`.
The unique index and transaction-scoped Postgres advisory lock (SQLite write
lock locally) serialize overlapping invocations. The lock is held throughout
each send/checkpoint. `details` contains message plan, fixture IDs, destination,
confirmed message IDs, reason and retryability; it never contains credentials.

Statuses are `waiting`, `running` for a partial post, `success` only after every
part is confirmed, and `failed`. A failed Telegram call remains retryable.
Confirmed parts are skipped. Part boundaries preserve whole fixtures and stay
below Telegram's limit using plain text. Unsent plans can be rebuilt from
fresh persisted data; after partial delivery a changed plan halts for operator
review so a stale frozen plan is not silently continued. Changing the channel
also requires review. A migrated V1 successful post is skipped.

Telegram provides no client idempotency key for `sendMessage`. If it accepts a
message but confirmation is lost, or the process/database fails before the
checkpoint commits, the next retry can duplicate that part. This residual risk
is tested and cannot be eliminated with a database claim alone. Transactions
that fail to commit must not be treated as confirmed delivery. Inspect the
channel after an ambiguous failure before allowing a retry.

The HTTP response lists each checked identity and its outcome (`waiting`,
`already-sent`, `no-matches`, `partial`, `sent`, `expired`, `plan-changed`, etc.).
Expected waiting/expiry states return HTTP 200 with explicit outcomes; actual
Telegram failures return 502 and configuration/database failures return 503.
Cron enqueue success is not proof of worker or Telegram delivery.

## Dry runs and integration procedure

Both CLI dry runs use read-only database connections, perform no Telegram
calls or refreshes, create no marker and make no schema/prediction writes.
Dates are examples; replace them with the actual persisted fixture day.

```powershell
python main.py league-telegram PL --publication MORNING --date 2026-10-10 --dry-run
python main.py league-telegram PL --publication RESULTS --date 2026-10-10 --dry-run
```

Readiness is logged separately from the exact preview. A saved message plan is
shown after confirmed delivery begins; unsent plans reflect current persisted
data, and real retries skip already confirmed parts. An unfinished
results dry run shows awaiting-confirmation text and is **not** publishable.

After implementation review, use a staging database and dedicated test channel
first. Deploy separately, review/apply the migration, set secrets, run the
existing league refresh, and verify the paused Cron configuration. Set the
publisher enabled on the intended host only when ready. To test authenticated
HTTP without sending, use environment values rather than pasting credentials:

```powershell
$telegramHeaders = @{ Authorization = "Bearer $env:TELEGRAM_PUBLISHER_SECRET" }
Invoke-RestMethod -Method Post -Uri $env:TELEGRAM_WORKER_URL -Headers $telegramHeaders -ContentType 'application/json' -Body '{"dry_run":true}'
```

Test missing/invalid auth returns 401 with no DB/Telegram activity. During the
appropriate window, review the preview/readiness and then explicitly issue a
manual integration call with `'{"dry_run":false}'`. It may need subsequent
calls for multipart completion. Verify message IDs and success in `agent_runs`;
repeat the call and confirm no duplicate. Test a controlled failed send and
retry on staging, followed by results availability after midnight. Activate
Cron through its Dashboard only after these checks and explicit approval.
**None of these production/integration actions were executed by Codex.**

Manual CLI live commands (also enforce windows/readiness):

```powershell
python main.py league-telegram PL --publication MORNING
python main.py league-telegram PL --publication RESULTS --date 2026-10-10
```

The Telegram GitHub workflow is now **manual-only**, defaults to dry run, and
does no refresh itself. Hourly `sportsintel-league.yml` and manual FIFA workflow
remain unchanged. Do not enable a second Telegram scheduler.

## Monitoring and recovery

Monitor all three layers: `cron.job_run_details`, pg_net HTTP outcomes, and
durable publication state/worker logs. Supabase retains pg_net responses for a
limited time; inspect promptly and retain operational alerts separately if
required. A simple scheduled HTTP job does not itself send operator alerts.

```sql
SELECT command,status,started_at,completed_at,
       details::jsonb->>'reason' AS reason,
       details::jsonb->'delivered' AS telegram_message_ids
FROM agent_runs
WHERE command LIKE 'PL:%:MORNING' OR command LIKE 'PL:%:RESULTS'
ORDER BY started_at DESC;

SELECT completed_at,status FROM agent_runs
WHERE command='league-fixtures-refresh:PL'
ORDER BY started_at DESC LIMIT 10;

SELECT jobname,active,schedule FROM cron.job
WHERE jobname='sportsintel-telegram-v2';

SELECT id,status_code,timed_out,error_msg,created
FROM net._http_response ORDER BY created DESC LIMIT 20;
```

Expired data/results create visible failed status and log an error. Pause the
Cron job while investigating. Check the hourly league refresh, API access,
freshness timestamps, final-score/status fields and channel after ambiguous
sends. Repair the upstream source and run the existing refresh; never manually
invent final results or change official probabilities. Do not delete successful
publication history, clear confirmed message IDs or reset a plan blindly.

For late RESULTS only, preview then recover explicitly:

```powershell
python main.py league-telegram PL --publication RESULTS --date 2026-10-10 --dry-run
python main.py league-telegram PL --publication RESULTS --date 2026-10-10 --recover
```

Recovery bypasses the results window only; freshness, confirmed scores,
official-pick eligibility, identity, destination and checkpoints still apply.
It cannot bypass morning kickoff protection or automatically resolve a changed
partial plan. For the latter, inspect delivered messages and persisted data,
then make a reviewed correction publication rather than deleting the history.
If Cron was unavailable for several days, query older `waiting`/`running` rows
and recover the explicit dates; automatic checks target today/yesterday, not an
unbounded historical backlog.

## Final remediation and deployment file set

`TELEGRAM_PUBLISHER_ENABLED` is enforced at the shared live publication service
and again at the SportsIntelAI Telegram transport. False, missing and invalid
values block all V2 live sends, including manual dispatch and recovery.
FIFA has the independent `FIFA_TELEGRAM_ENABLED` switch, defaulting to false.
Keep it false or unset: enabling SportsIntelAI cannot enable FIFA publishing.
FIFA `daily` still refreshes and rebuilds its bracket while its briefing is
printed as a preview. The FIFA workflow is manual-only, with no automatic
Telegram triggers. FIFA routes, templates and prediction engine are preserved.
Authenticated HTTP and CLI dry runs remain available
while disabled. Disabled service attempts create no publication records and
leave existing checkpoints unchanged. A switch change cannot recall a request
already in flight.

The manual GitHub workflow reads the repository variable of the same name,
defaulting to false. Vercel environment settings do not propagate to GitHub or
local processes: keep the switch false on every execution host during rollout.
There is no dispatch input or recovery option that bypasses it.

Results use persisted fixture IDs and the current Europe/London kickoff date.
Old morning fixture IDs retain postponement/rescheduling notices only. Confirmed
results parts record `result_fixture_ids_by_part` in existing `agent_runs.details`;
other dates cannot score those fixtures again after subsequent date corrections.
League-wide results transaction locking protects concurrent different-day calls.
Only confirmed parts reserve IDs; postponement notices do not reserve scores.
Existing successful publication identities remain skipped. Historical results
written by an older version without per-part fixture metadata cannot provide this
additional cross-date protection retroactively; inspect any such already-sent
results before changing completed fixtures' dates. No production migration is
needed: the applied unique index and existing details JSON remain compatible.

A fixture move after partial morning delivery stops continuation for review and
preserves the frozen messages/checkpoints. Fully delivered morning history stays
unchanged. Neither case assigns a score to the old day. Prediction probabilities,
locks and evaluated records are never rewritten by the publisher.

The exact changed/new files to include in the deployment commit are:

```text
.env.example
.gitignore
README.md
requirements.txt
main.py
src/database.py
src/league_service.py
src/web_app.py
src/telegram_bot.py
src/league_telegram.py
src/league_publications.py
src/telegram_worker.py
.github/workflows/league_telegram.yml
sql/telegram_publications_v2.sql
sql/supabase_telegram_preflight.sql
sql/supabase_telegram_cron.sql
docs/telegram_publishing_v2.md
tests/test_league_dashboard_performance.py
tests/test_league_telegram.py
tests/test_telegram_publications.py
```

Exclude `static/styles.css` and all `research/` files. Include no `.env`, local
databases, journals, caches or bytecode. Stage explicit paths rather than using
`git add .`. The SQL files are deployment documentation; committing them does
not apply migrations or activate Cron.

## Verification

Run `python -m pytest tests -q -p no:cacheprovider`. Tests use SQLite and mocks
for Telegram and HTTP scheduling calls. They cover time windows, BST/GMT,
midnight/delayed results, official eligibility, freshness, idempotency and
concurrency, partial retries, migration, authentication, HTTPS, dry-run safety,
and the existing league/FIFA suite. Production extension availability,
Postgres locking/migration, Vercel execution settings and pg_net transport still
require the reviewed integration procedure.
