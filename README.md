# SportsIntelAI

SportsIntelAI now contains two deliberately separate agents:

- **V1 — FIFA 2026 Dynamic Bracket Agent:** the original rule-based tournament engine, snapshots, Telegram briefing, CLI and archive at `/fifa-2026`.
- **V2 — Multi-League Match Prediction Agent:** the Premier League machine-learning pipeline and public dashboard at `/` and `/league`.

A transparent, offline-first Python agent that merges actual knockout results with predictions, rebuilds every downstream World Cup path, stores daily snapshots in SQLite, and prepares a Telegram briefing.

## V2 architecture and integrity

V2 is configuration-driven (`src/league_config.py`) and shares ingestion, feature, model and evaluation code across competitions. Only `PL` is enabled; adding a code to source is intentional so an unsupported API plan is never advertised.

The pipeline is: football-data.org → `league_matches`/official standings → chronological features → time-split training → provisional/locked lifecycle → evaluation → public read-only dashboard. API keys stay server-side.

The baseline is scikit-learn multinomial logistic regression with scaling and Home/Draw/Away probabilities. A deployment-friendly `HistGradientBoostingClassifier` challenger is evaluated on the same chronological split; XGBoost is not required, avoiding an extra native runtime dependency. Training uses the oldest 70%, the next 15% for model selection, and the newest 15% as the untouched test set. Stored metrics include accuracy, explicit multiclass log loss and multiclass Brier score. The small, non-secret production artifact is intentionally versioned at `models/pl_model.pkl`; scikit-learn is pinned to its training version.

Features are computed in kickoff order using only strictly earlier completed matches: Elo difference, recent points/goals, goal-difference comparisons, home/away venue form, rest difference and matchday. Form and Elo carry across season boundaries. Clubs without Premier League history use documented neutral form and 1500 Elo fallbacks; no fabricated lower-league results are added. Elo adds 80 points to the home side for expectation only and uses `K=20`: `R' = R + K × (actual − expected)`, with `expected = 1/(1+10^((opponent−rating)/400))`.

`league-history` spaces requests and retries transient failures/HTTP 429 responses. Match IDs are primary keys, so reruns update rather than duplicate rows. `league-daily` fetches current data, evaluates locked results and predicts newly available fixtures, but deliberately refuses to retrain: production retraining remains an explicit `league-train` operation. The dashboard keeps historical backtest metrics separate from live locked-prediction accuracy.

Predictions use a two-stage lifecycle. Fixtures more than 48 hours away receive refreshable **Provisional** previews. Each daily run rebuilds those previews from the latest chronological Elo and rolling form. On entering the 48-hour window the row becomes a **Locked / Official Agent Pick**; its probabilities, outcome and feature audit can never change. Nothing can be created or modified after kickoff. Only locked picks can become evaluated and count toward live accuracy.

### V2 commands

```powershell
python main.py league-fetch PL --season 2025  # one football-data.org season
python main.py league-history PL     # 2023-25 history plus current season
python main.py league-history PL --from-season 2021 --to-season 2025
python main.py league-train PL       # chronological 70/15/15 evaluation
python main.py league-predict PL     # refresh provisional and lock near-term picks
python main.py league-evaluate PL    # score newly finished matches
python main.py league-daily PL       # fetch, evaluate, refresh provisional/locked picks
python main.py web                    # V2 at /; FIFA archive at /fifa-2026
```

Set `FOOTBALL_DATA_API_KEY` in `.env`; do not commit it. Historical availability depends on the football-data.org subscription. The project never fabricates matches: if fewer than 12 completed matches covering all outcomes are stored, training stops with an honest error. A future CSV adapter can feed the same normalized `league_matches` interface.

Public read-only endpoints are `/api/leagues/PL/matches?date=YYYY-MM-DD`, `/standings`, and `/performance`. Production uses the same FastAPI application on Vercel, Supabase Postgres for durable state, and GitHub Actions for scheduled agent work. Local development continues to use SQLite automatically when `DATABASE_URL` is absent.

### Limitations

V2 currently uses results and schedule data only. It does **not** claim xG, injuries, lineups, betting odds, exact-score predictions or fake live updates. Current API standings are displayed but are not used as historical features, avoiding past/future leakage. Confidence labels are Low below 50%, Medium at 50–64.9%, and High at 65%+; they are labels, not certainty.

The included fixture file is **illustrative demo data**, not an official 2026 feed. Set `USE_SAMPLE_DATA=false` and configure football-data.org for live use.

## What it does

- Fetches a configured JSON API and falls back to local fixtures, then cached SQLite data.
- Keeps completed results fixed—even if a later stale feed marks them scheduled.
- Resolves every future bracket slot from its source match.
- Removes knockout losers from all future prediction paths.
- Scores teams using local strength, recent win rate, goals, knockout wins, opponent quality, and matchup strength.
- Handles drawn knockout results through penalty scores or an explicit `winner_team`.
- Saves every rebuild in `predictions`, `bracket_snapshots`, and `agent_runs`.
- Prints a complete real/predicted bracket and generates a six-section Telegram update.

## Quick start

Requires Python 3.10 or later.

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python main.py daily
```

On the first run, `daily` loads `data/sample_fixtures.json`, creates `data/fifa2026.db`, rebuilds all 31 knockout matches, prints the full bracket, and prints a Telegram preview when credentials are absent.

## Commands

```powershell
python main.py fetch                 # API -> local fixture -> DB-cache fallback
python main.py update-bracket        # rebuild and persist one snapshot
python main.py predict               # rebuild, persist, and show predictions
python main.py send                  # send latest snapshot or print preview
python main.py daily                 # fetch + rebuild + Telegram/preview
python main.py snapshot --limit 10   # snapshot history and change summaries
python main.py web                   # dashboard at http://localhost:8000
```

## Browser dashboard

Run `python main.py web`, then open [http://localhost:8000](http://localhost:8000) for Premier League predictions. The FIFA tournament archive remains available at `/fifa-2026`.

### Showcase and Reel Mode

- **Final Path** follows the predicted champion through one quarter-final, one semi-final, and the final.
- **From Quarter-finals** shows all four quarter-finals, both semi-finals, the final, and champion card.
- **Full Bracket** restores every knockout round in a compact analytics view.
- **Reel Mode** hides supporting panels, leaving the summary, bracket, and champion treatment for clean 16:9 or vertical capture.
- **Copy Telegram Summary** copies the generated briefing without sending anything.

Use `--verbose` to expose every match resolution and model update in the logs.

## Configure football-data.org live fixtures

Copy `.env.example` to `.env` and set:

```dotenv
USE_SAMPLE_DATA=false
FOOTBALL_DATA_API_KEY=your-football-data-token
FOOTBALL_DATA_BASE_URL=https://api.football-data.org/v4
FOOTBALL_DATA_COMPETITION=WC
```

Live mode requests `/competitions/WC/matches` with the `X-Auth-Token` header. If the API is temporarily unavailable, the agent may use matches already cached in SQLite, but it will never load `sample_fixtures.json` while `USE_SAMPLE_DATA=false`.

The adapter understands this project's fixture schema and common football-data-style fields (`homeTeam`, `awayTeam`, `score.fullTime`, and stage codes such as `LAST_16`). For a provider with different fields, adjust only `FixtureAPIClient._adapt()` in `src/api_client.py`.

Future matches use explicit bracket links:

```json
{
  "id": "QF-01",
  "stage": "Quarter-final",
  "status": "scheduled",
  "home_source_match": "R16-01",
  "away_source_match": "R16-02"
}
```

That graph is what makes an upset propagate. If France was predicted through `R16-02` but Brazil actually beat France, `R16-02` resolves to Brazil; every quarter-final, semi-final, final, and champion prediction downstream is rebuilt from that actual winner.

## Demo an upset

1. Run `python main.py daily` to create a baseline snapshot.
2. Change one scheduled fixture in `data/sample_fixtures.json` to `completed` and add scores.
3. Run `python main.py daily` again.
4. Run `python main.py snapshot` to show the before/after winner path.

The briefing will name the newly eliminated team, the actual advancing team, future fixtures whose participants/winners changed, and any champion change.

## Telegram

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env`. Without them, `send` remains safe for reels and demos: it prints the exact message instead of making a network call.

For daily automation, schedule this from Windows Task Scheduler:

```powershell
python D:\fifa2026\main.py daily
```

Set the task's working directory to `D:\fifa2026`.

## Production architecture: Vercel + Supabase + GitHub Actions

Vercel serves `api/index.py` as a stateless FastAPI function. Jinja templates, static assets, the committed model artifact, and non-secret JSON resources are bundled through `vercel.json`. Vercel cold starts do not bootstrap data or run the in-process scheduler; public routes only read durable state from Supabase.

Supabase Postgres is the production system of record. The database adapter selects Postgres when `DATABASE_URL` exists and otherwise retains the existing local SQLite behavior. Postgres connections are short-lived and prepared statements are disabled, which makes the Supabase transaction pooler appropriate for Vercel and GitHub Actions.

Deployment steps:

1. Create a Supabase project and copy its database connection string. Use a direct or session-pooler connection for the one-time migration; use the transaction-pooler string (normally port 6543) for Vercel and the scheduled workflow.
2. Set `DATABASE_URL` locally without committing it, then migrate the current SQLite state idempotently:

   ```powershell
   $env:DATABASE_URL="postgresql://..."
   python main.py migrate-db --source-database data/fifa2026.db
   ```

   The command copies every V1 and V2 table, preserves primary keys, prediction statuses, timestamps, evaluated results, model versions, snapshots, and run history, and reports source/inserted/target counts. It can be rerun safely.
3. Add Vercel environment variable `DATABASE_URL` using the Supabase transaction-pooler URL. Do not set `ENABLE_LEAGUE_SCHEDULER` or `BOOTSTRAP_LEAGUE_DATA` on Vercel.
4. Import the Git repository into Vercel and deploy. `vercel.json` routes all requests to FastAPI.
5. Add GitHub repository secrets `DATABASE_URL` and `FOOTBALL_DATA_API_KEY`. Optionally add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
6. Run the **SportsIntelAI League Agent** workflow manually once. It then runs `python main.py league-daily PL` at 08:00 and 20:00 UTC, with overlapping runs prevented by workflow concurrency.
7. Verify `/health` reports `status: ok` and `database: connected`, then check `/`, `/league`, and `/fifa-2026`.

The football-data.org key is needed only by GitHub Actions/CLI ingestion, not by public page requests. Never commit Supabase credentials or API tokens. The existing `render.yaml` remains as an optional alternative deployment path, not the primary architecture.

## Database model

- `teams`: strength, qualification, and elimination state.
- `matches`: actual/confirmed fixtures plus source-match links.
- `predictions`: one transparent prediction per match and run.
- `bracket_snapshots`: the complete JSON bracket at each rebuild.
- `agent_runs`: command status and human-readable update summary.

Completed results are never overwritten by a non-completed copy from a stale feed. Re-running the same input creates an auditable snapshot but does not mutate the actual result.
