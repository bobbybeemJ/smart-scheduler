"""One-time (or periodic - see the 7-day refresh-token expiry risk for apps still in OAuth
Testing status) local OAuth consent flow. Prints the refresh token + client id/secret so they
can be copied into .env and into the deployed service's env vars. The deployed container's
filesystem is ephemeral, so the app never reads a token.json - it reconstructs credentials from
these env vars at startup instead.

Prerequisites:
- A GCP project with the Calendar API enabled.
- An OAuth consent screen in Testing status with your Google account added as a test user.
- An OAuth client ID of type "Desktop app", downloaded as client_secret.json into the repo root
  (gitignored - never commit this file).
"""

import json
import pathlib

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/calendar.readonly",
]

CLIENT_SECRET_FILE = pathlib.Path(__file__).resolve().parent.parent / "client_secret.json"
LOCAL_TOKEN_CACHE = pathlib.Path(__file__).resolve().parent.parent / "token.json"


def main():
    if not CLIENT_SECRET_FILE.exists():
        raise SystemExit(
            f"Missing {CLIENT_SECRET_FILE}. Download the OAuth Desktop app client secret "
            "JSON from Google Cloud Console and place it there (it's gitignored)."
        )

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    creds = flow.run_local_server(port=0)

    # Local convenience cache only - never relied on by the deployed app.
    LOCAL_TOKEN_CACHE.write_text(creds.to_json())

    print("\n=== Copy these into .env (local) and the Render dashboard env vars (deploy) ===\n")
    print(f"GOOGLE_OAUTH_CLIENT_ID={creds.client_id}")
    print(f"GOOGLE_OAUTH_CLIENT_SECRET={creds.client_secret}")
    print(f"GOOGLE_OAUTH_REFRESH_TOKEN={creds.refresh_token}")
    print(
        "\nReminder: Google caps refresh-token lifetime to ~7 days for unverified/Testing-mode "
        "apps requesting sensitive scopes. Re-run this script close to the demo recording date."
    )


if __name__ == "__main__":
    main()
