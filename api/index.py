"""Vercel Python entrypoint; the application remains a single FastAPI function."""

from src.web_app import app

__all__ = ["app"]
