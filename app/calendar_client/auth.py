"""Builds OAuth credentials purely from env vars - never reads a local token.json. This is what
lets the deployed app survive Render's ephemeral filesystem (a restart wipes any local file, but
env vars persist across restarts)."""

from __future__ import annotations

import os

from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

REQUIRED_ENV_VARS = [
    "GOOGLE_OAUTH_CLIENT_ID",
    "GOOGLE_OAUTH_CLIENT_SECRET",
    "GOOGLE_OAUTH_REFRESH_TOKEN",
]


def build_credentials_from_env() -> Credentials:
    missing = [name for name in REQUIRED_ENV_VARS if not os.environ.get(name)]
    if missing:
        raise RuntimeError(
            f"Missing required env vars: {missing}. Run scripts/oauth_bootstrap.py and copy "
            "the printed values into .env (or the Render dashboard env vars for deploy)."
        )

    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        scopes=SCOPES,
    )
