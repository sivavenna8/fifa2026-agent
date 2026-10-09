from __future__ import annotations

import json
import os
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .config import parse_bool


class TelegramError(RuntimeError):
    pass


def publishing_enabled(publisher='sportsintel'):
    """Independent publisher switches fail closed, including malformed values."""
    flags = {'sportsintel': 'TELEGRAM_PUBLISHER_ENABLED', 'fifa': 'FIFA_TELEGRAM_ENABLED'}
    if publisher not in flags:
        return False
    try:
        return parse_bool(os.getenv(flags[publisher]), False)
    except ValueError:
        return False


def send_message(token: str, chat_id: str, message: str, timeout: int = 15, *, publisher='sportsintel') -> dict:
    if not publishing_enabled(publisher):
        raise TelegramError('Publisher is disabled')
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    request = Request(url, data=urlencode({"chat_id": chat_id, "text": message}).encode("utf-8"))
    try:
        with urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                raise TelegramError(f"Telegram HTTP status {response.status}")
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise TelegramError(f"Telegram HTTP status {exc.code}") from None
    except (URLError, OSError, TimeoutError, ValueError, UnicodeError, HTTPException):
        raise TelegramError("Telegram network failure, timeout, or malformed response") from None
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise TelegramError("Telegram rejected the message or returned a malformed response")
    delivered = result.get("result")
    if not isinstance(delivered, dict) or not isinstance(delivered.get("message_id"), int):
        raise TelegramError("Telegram response did not confirm message delivery")
    return result
