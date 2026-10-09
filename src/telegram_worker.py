"""Authenticated synchronous worker mounted in the existing Vercel FastAPI app."""
from __future__ import annotations

import hmac
import logging
import os
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .config import get_settings
from .database import Database
from .league_publications import publishing_enabled, tick
from .telegram_bot import TelegramError

LOGGER = logging.getLogger(__name__)
router = APIRouter()


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dry_run: bool = False


@router.post('/internal/telegram/check', include_in_schema=False)
def check(request: Request, body: CheckRequest):
    # Vercel terminates TLS at its trusted edge; local HTTP is not accepted.
    https = request.url.scheme == 'https' or (bool(os.getenv('VERCEL')) and request.headers.get('x-forwarded-proto') == 'https')
    if not https:
        raise HTTPException(403, 'HTTPS is required')
    secret = os.getenv('TELEGRAM_PUBLISHER_SECRET', '').strip()
    if not secret:
        raise HTTPException(503, 'Publisher authentication is not configured')
    authorization = request.headers.get('authorization', '')
    if not hmac.compare_digest(authorization.encode(), f'Bearer {secret}'.encode()):
        raise HTTPException(401, 'Unauthorized')
    if not body.dry_run and not publishing_enabled():
        raise HTTPException(503, 'Publisher is disabled')
    settings = get_settings()
    if not settings.database_url:
        raise HTTPException(503, 'Shared production database is required')
    db = Database(settings.database_path, settings.database_url, initialize_schema=False,
                  read_only=body.dry_run, bounded_worker=True)
    try:
        outcomes = tick(db, dry_run=body.dry_run)
    except TelegramError:
        LOGGER.error('[telegram-agent] HTTP worker delivery failed; retry is available')
        raise HTTPException(502, 'Telegram delivery failed; inspect publication status') from None
    except Exception as exc:
        LOGGER.error('[telegram-agent] HTTP worker failed (%s)', type(exc).__name__)
        raise HTTPException(503, 'Publisher unavailable; inspect configuration/data') from None
    response = {'publications': outcomes}
    if body.dry_run:
        commit = os.getenv('VERCEL_GIT_COMMIT_SHA', '').strip()
        response.update(
            publisher_enabled=publishing_enabled(),
            deployment_commit=commit if re.fullmatch(r'[0-9a-fA-F]{40}|[0-9a-fA-F]{64}', commit) else None,
        )
    return response
