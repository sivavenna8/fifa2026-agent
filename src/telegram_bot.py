from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class TelegramError(RuntimeError):
    pass


def send_message(token: str, chat_id: str, message: str, timeout: int = 15) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    request = Request(url, data=urlencode({"chat_id": chat_id, "text": message}).encode("utf-8"))
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise TelegramError(str(exc)) from exc
    if not result.get("ok"):
        raise TelegramError(result.get("description", "Telegram rejected the message"))
    return result
