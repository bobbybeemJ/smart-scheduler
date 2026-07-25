"""Builds OAuth credentials purely from settings (never reads a local token.json). This is what
lets the deployed app survive Render's ephemeral filesystem (a restart wipes any local file, but
env vars persist across restarts).

Reads from app.config.settings, not raw os.environ - found via a real bug where the actual
server (uvicorn app.main:app, no load_dotenv() anywhere in its import chain) couldn't see the
OAuth env vars at all, even though .env had them and every standalone dev script worked fine
(each one calls load_dotenv() itself). pydantic-settings parses .env into `settings` regardless
of whether load_dotenv() was ever called, so routing through it here removes the dependency
entirely instead of papering over it with another load_dotenv() call."""

from __future__ import annotations

from google.oauth2.credentials import Credentials

from app.config import settings

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def build_credentials_from_env() -> Credentials:
    required = {
        "GOOGLE_OAUTH_CLIENT_ID": settings.google_oauth_client_id,
        "GOOGLE_OAUTH_CLIENT_SECRET": settings.google_oauth_client_secret,
        "GOOGLE_OAUTH_REFRESH_TOKEN": settings.google_oauth_refresh_token,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise RuntimeError(
            f"Missing required settings: {missing}. Run scripts/oauth_bootstrap.py and copy "
            "the printed values into .env (or the Render dashboard env vars for deploy)."
        )

    return Credentials(
        token=None,
        refresh_token=settings.google_oauth_refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=settings.google_oauth_client_id,
        client_secret=settings.google_oauth_client_secret,
        scopes=SCOPES,
    )
