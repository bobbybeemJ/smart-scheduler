"""Phase 0 sanity check: simulates what happens after a Render restart, where the filesystem
is wiped and any local token.json is gone. Temporarily hides token.json (if present) and proves
credentials can still be built and used from env vars alone."""

import os
import pathlib
import sys

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from check_oauth_list_calendars import build_credentials_from_env  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

TOKEN_FILE = pathlib.Path(__file__).resolve().parent.parent.parent / "token.json"


def main():
    hidden = None
    if TOKEN_FILE.exists():
        hidden = TOKEN_FILE.with_suffix(".json.hidden_for_test")
        TOKEN_FILE.rename(hidden)
        print(f"Temporarily moved {TOKEN_FILE.name} aside to simulate an ephemeral filesystem.")

    try:
        assert not TOKEN_FILE.exists(), "token.json still present - test is not isolated"
        creds = build_credentials_from_env()
        service = build("calendar", "v3", credentials=creds)
        result = service.calendarList().list().execute()
        print(
            f"OK: reconstructed credentials from env vars alone (no token.json) and listed "
            f"{len(result.get('items', []))} calendar(s)."
        )
    finally:
        if hidden is not None:
            hidden.rename(TOKEN_FILE)
            print(f"Restored {TOKEN_FILE.name}.")


if __name__ == "__main__":
    main()
