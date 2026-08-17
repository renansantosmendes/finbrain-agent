"""Vercel entrypoint.

Vercel's Python runtime looks for an ASGI/WSGI `app` object under `api/`.
The real FastAPI app lives in `app.py` at the project root (also used for
local dev via `python app.py`) -- this file just re-exports it so Vercel can
find it without duplicating the app definition.
"""
from app import app  # noqa: F401
