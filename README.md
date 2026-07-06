# FIFA2026 Dynamic Bracket Agent

A transparent, offline-first Python agent that merges actual knockout results with predictions, rebuilds every downstream World Cup path, stores daily snapshots in SQLite, and prepares a Telegram briefing.

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

Requires Python 3.10 or later. No third-party packages are required.

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

Run `python main.py web`, then open [http://localhost:8000](http://localhost:8000). The responsive World Cup-style dashboard reads directly from SQLite and shows the latest run, actual results, eliminated teams, every bracket round, strength badges, snapshot changes, the projected champion, and the current Telegram briefing preview. Use `python main.py daily` to fetch and rebuild; refresh the dashboard to display the new snapshot.

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

## Deploy on Render

The repository includes a Render Blueprint at `render.yaml`. It uses this production command:

```text
uvicorn src.web_app:app --host 0.0.0.0 --port $PORT
```

Deployment steps:

1. Push the project to a GitHub, GitLab, or Bitbucket repository.
2. In Render, choose **New → Blueprint** and select the repository.
3. When prompted for `FOOTBALL_DATA_API_KEY`, enter the football-data.org token. It is configured with `sync: false`, so the secret is stored by Render and is not committed to the repository.
4. Apply the Blueprint and wait for `/health` to report healthy.
5. Open the generated `onrender.com` URL.

The Blueprint mounts a 1 GB persistent disk at `/var/data` and sets `DATABASE_PATH=/var/data/fifa2026.db`. Persistent disks require a paid Render web-service plan; this preserves bracket snapshots across deploys and restarts. To experiment on a free ephemeral service, remove the `disk` block and change `DATABASE_PATH` to `data/fifa2026.db`, understanding that the SQLite history will reset on a redeploy or restart.

On startup, `src.web_app:app` opens SQLite. If no matches exist it fetches the configured football-data.org competition, and if no snapshot exists it builds the first bracket before serving traffic. Runtime environment variables take precedence over local `.env` values. Never add the real API key—or any Telegram credentials—to `render.yaml`, `.env.example`, or source control.

## SQLite model

- `teams`: strength, qualification, and elimination state.
- `matches`: actual/confirmed fixtures plus source-match links.
- `predictions`: one transparent prediction per match and run.
- `bracket_snapshots`: the complete JSON bracket at each rebuild.
- `agent_runs`: command status and human-readable update summary.

Completed results are never overwritten by a non-completed copy from a stale feed. Re-running the same input creates an auditable snapshot but does not mutate the actual result.
