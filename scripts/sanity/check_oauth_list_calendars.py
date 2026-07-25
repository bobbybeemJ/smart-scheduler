"""Phase 0 sanity check: confirms OAuth works end to end by listing your real calendars.
Builds credentials purely from env vars (client id/secret/refresh token) - no token.json read -
so this also doubles as a first pass at the "reconstruct from env var alone" requirement.
Run scripts/oauth_bootstrap.py first to populate .env."""

import os

from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

load_dotenv()

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]


def build_credentials_from_env() -> Credentials:
    required = ["GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_OAUTH_REFRESH_TOKEN"]
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Missing env vars: {missing}. Run scripts/oauth_bootstrap.py first.")

    return Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        scopes=SCOPES,
    )


def main():
    creds = build_credentials_from_env()
    service = build("calendar", "v3", credentials=creds)
    result = service.calendarList().list().execute()

    calendars = result.get("items", [])
    print(f"Found {len(calendars)} calendar(s):")
    for cal in calendars:
        print(f"  - {cal.get('summary')} ({cal.get('id')})")


if __name__ == "__main__":
    main()
