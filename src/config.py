from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FOOTBALL_DATA_BASE_URL = "https://api.football-data.org/v4"


def parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value {value!r}; use true or false")


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


def get_settings() -> Settings:
    load_env()
    use_sample_data = parse_bool(os.getenv("USE_SAMPLE_DATA"), default=True)
    base_url = os.getenv("FOOTBALL_DATA_BASE_URL", DEFAULT_FOOTBALL_DATA_BASE_URL).rstrip("/")
    competition = os.getenv("FOOTBALL_DATA_COMPETITION", "WC").strip() or "WC"
    football_data_url = f"{base_url}/competitions/{quote(competition, safe='')}/matches"
    return Settings(
        database_path=Path(os.getenv("DATABASE_PATH", ROOT / "data" / "fifa2026.db")),
        strengths_path=Path(os.getenv("TEAM_STRENGTH_PATH", ROOT / "data" / "team_strength.json")),
        fixtures_path=Path(os.getenv("FIXTURES_PATH", ROOT / "data" / "sample_fixtures.json")),
        use_sample_data=use_sample_data,
        api_url=(os.getenv("FIFA_API_URL") or football_data_url) if not use_sample_data else None,
        api_key=os.getenv("FOOTBALL_DATA_API_KEY") or os.getenv("FIFA_API_KEY") or None,
        football_data_competition=competition,
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID") or None,
        request_timeout=int(os.getenv("REQUEST_TIMEOUT", "15")),
    )
