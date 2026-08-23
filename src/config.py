from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"


def env_value(name: str, default: str | None = None) -> str | None:
    """Return a stripped environment value, treating blank values as missing."""
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip()


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value {value!r}; use true or false")


def parse_int(value: str | None, default: int, name: str) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value.strip())
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer; received {value!r}") from exc


def parse_schedule_hours(value: str | None, default: tuple[int, ...] = (8, 20)) -> tuple[int, ...]:
    if value is None or not value.strip():
        return default
    try:
        hours = tuple(sorted({int(item.strip()) for item in value.split(",") if item.strip()}))
    except ValueError as exc:
        raise ValueError(
            f"LEAGUE_SCHEDULE_HOURS_UTC must contain comma-separated integer hours; received {value!r}"
        ) from exc
    if not hours or any(hour < 0 or hour > 23 for hour in hours):
        raise ValueError("LEAGUE_SCHEDULE_HOURS_UTC must contain hours from 0 to 23")
    return hours


def load_env(path: Path | None = None) -> None:
    """Load a small .env file without requiring an external package."""
    env_path = path or ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    database_path: Path
    strengths_path: Path
    fixtures_path: Path
    use_sample_data: bool
    api_url: str | None
    api_key: str | None
    football_data_competition: str
    telegram_token: str | None
    telegram_chat_id: str | None
    request_timeout: int
    enable_league_scheduler: bool = False
    bootstrap_league_data: bool = False
    league_schedule_hours: tuple[int, ...] = (8, 20)
    database_url: str | None = None


def get_settings() -> Settings:
    load_env()
    use_sample_data = parse_bool(env_value("USE_SAMPLE_DATA"), default=True)
    base_url = env_value("FOOTBALL_DATA_BASE_URL", DEFAULT_FOOTBALL_DATA_BASE_URL).rstrip("/")
    competition = env_value("FOOTBALL_DATA_COMPETITION", "WC")
    football_data_url = f"{base_url}/competitions/{quote(competition, safe='')}/matches"
    schedule_hours = parse_schedule_hours(env_value("LEAGUE_SCHEDULE_HOURS_UTC"))
    return Settings(
        database_path=Path(env_value("DATABASE_PATH", str(ROOT / "data" / "fifa2026.db"))),
        strengths_path=Path(env_value("TEAM_STRENGTH_PATH", str(ROOT / "data" / "team_strength.json"))),
        fixtures_path=Path(env_value("FIXTURES_PATH", str(ROOT / "data" / "sample_fixtures.json"))),
        use_sample_data=use_sample_data,
        api_url=(env_value("FIFA_API_URL") or football_data_url) if not use_sample_data else None,
        api_key=env_value("FOOTBALL_DATA_API_KEY") or env_value("FIFA_API_KEY"),
        football_data_competition=competition,
        telegram_token=env_value("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=env_value("TELEGRAM_CHAT_ID"),
        request_timeout=parse_int(env_value("REQUEST_TIMEOUT"), 15, "REQUEST_TIMEOUT"),
        enable_league_scheduler=parse_bool(env_value("ENABLE_LEAGUE_SCHEDULER"), False),
        bootstrap_league_data=parse_bool(env_value("BOOTSTRAP_LEAGUE_DATA"), False),
        league_schedule_hours=schedule_hours,
        database_url=env_value("DATABASE_URL"),
    )
