from __future__ import annotations

import argparse
import logging
import sys

from src.api_client import FixtureAPIClient, FixtureAPIError
from src.bracket_engine import BracketEngine, BracketError, predicted_champion
from src.config import get_settings
from src.database import Database
from src.fixtures_loader import load_fixtures, load_strengths
from src.message_builder import build_message, change_summary, compare_snapshots, render_table
from src.telegram_bot import TelegramError, send_message

LOGGER = logging.getLogger("fifa2026")


def configure_logging(verbose: bool = False) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def fetch_data(db: Database) -> str:
    settings = get_settings()
    strengths = load_strengths(settings.strengths_path)
    db.upsert_teams(strengths)
    if settings.use_sample_data:
        fixtures = load_fixtures(settings.fixtures_path)
        source = "local sample fixtures"
        LOGGER.info("USE_SAMPLE_DATA=true; loading %s", settings.fixtures_path)
    else:
        if not settings.api_url:
            raise FixtureAPIError("Live mode requires a football-data.org API URL")
        if not settings.api_key:
            raise FixtureAPIError("USE_SAMPLE_DATA=false requires FOOTBALL_DATA_API_KEY")
        LOGGER.info(
            "USE_SAMPLE_DATA=false; fetching football-data.org competition %s from %s",
            settings.football_data_competition,
            settings.api_url,
        )
        try:
            fixtures = FixtureAPIClient(settings.api_url, settings.api_key, settings.request_timeout).fetch()
            if not fixtures:
                raise FixtureAPIError("API returned no knockout fixtures")
            source = settings.api_url
        except FixtureAPIError as exc:
            if db.get_matches():
                LOGGER.warning("Live API unavailable (%s); using cached database matches (sample file was not loaded)", exc)
                return "cached database"
            raise
    inserted, updated = db.upsert_matches(fixtures)
    if not settings.use_sample_data:
        removed = db.retain_matches({match["id"] for match in fixtures})
        if removed:
            LOGGER.info("Removed %d stale non-live fixture rows from SQLite", removed)
    LOGGER.info("Fetched %d fixtures from %s: %d inserted, %d updated", len(fixtures), source, inserted, updated)
    return source


def rebuild(db: Database, command: str = "update-bracket", print_table: bool = True) -> list[dict]:
    run_id = db.start_run(command)
    try:
        prior = db.latest_snapshots(1)
        previous = prior[0]["bracket"] if prior else None
        bracket, eliminated, active = BracketEngine(db.get_matches(), db.get_teams()).rebuild()
        db.reset_team_statuses(eliminated, active)
        changes = compare_snapshots(bracket, previous)
        summary = change_summary(changes)
        champion = predicted_champion(bracket)
        db.save_predictions(run_id, bracket)
        db.save_snapshot(run_id, bracket, champion, summary)
        db.finish_run(run_id, "success", summary)
        LOGGER.info("Bracket rebuilt: %d matches, %d eliminated, predicted champion=%s", len(bracket), len(eliminated), champion)
        LOGGER.info("Update summary: %s", summary)
        if print_table:
            print(render_table(bracket))
            print(f"\nPredicted Winner: {champion}")
        return bracket
    except Exception as exc:
        db.finish_run(run_id, "failed", str(exc))
        raise


def send_latest(db: Database) -> None:
    settings = get_settings()
    snapshots = db.latest_snapshots(2)
    if not snapshots:
        bracket = rebuild(db, "send", print_table=False)
        previous = None
    else:
        bracket = snapshots[0]["bracket"]
        previous = snapshots[1]["bracket"] if len(snapshots) > 1 else None
    message = build_message(bracket, previous)
    if not settings.telegram_token or not settings.telegram_chat_id:
        LOGGER.warning("Telegram credentials are not configured; printing briefing preview")
        print(message)
        return
    send_message(settings.telegram_token, settings.telegram_chat_id, message, settings.request_timeout)
    LOGGER.info("Telegram briefing sent successfully")


def show_snapshots(db: Database, limit: int) -> None:
    snapshots = db.latest_snapshots(limit)
    if not snapshots:
        print("No snapshots yet. Run: python main.py update-bracket")
        return
    for snapshot in snapshots:
        print(f"#{snapshot['id']} | {snapshot['created_at']} | Winner: {snapshot['predicted_winner']} | {snapshot['change_summary']}")


def main() -> int:
    parser = argparse.ArgumentParser(description="FIFA 2026 Dynamic Bracket Agent")
    parser.add_argument("command", choices=("fetch", "update-bracket", "predict", "send", "daily", "snapshot", "web"))
    parser.add_argument("--limit", type=int, default=10, help="Snapshot rows to show")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    configure_logging(args.verbose)
    settings = get_settings()
    db = Database(settings.database_path)
    try:
        if args.command == "fetch":
            fetch_data(db)
        elif args.command in {"update-bracket", "predict"}:
            if not db.get_matches():
                fetch_data(db)
            rebuild(db, args.command)
        elif args.command == "send":
            send_latest(db)
        elif args.command == "daily":
            fetch_data(db)
            rebuild(db, "daily")
            send_latest(db)
        elif args.command == "snapshot":
            show_snapshots(db, args.limit)
        elif args.command == "web":
            if not db.get_matches():
                fetch_data(db)
                rebuild(db, "web-bootstrap", print_table=False)
            from src.web_app import create_app
            import uvicorn

            LOGGER.info("Dashboard available at http://localhost:8000")
            uvicorn.run(create_app(settings.database_path), host="0.0.0.0", port=8000, log_level="info")
        return 0
    except (OSError, ValueError, FixtureAPIError, BracketError, TelegramError) as exc:
        LOGGER.error("%s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
