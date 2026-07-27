"""Sanity check: simulates what happens after a container restart, where the filesystem is
wiped and any local token.json is gone. Temporarily hides token.json (if present) and proves
credentials can still be built and used from env vars alone via app.calendar_client.
Run as: python -m scripts.sanity.check_oauth_env_reconstruction"""

import pathlib

from dotenv import load_dotenv

load_dotenv()

from app.calendar_client.client import list_calendars  # noqa: E402

TOKEN_FILE = pathlib.Path(__file__).resolve().parent.parent.parent / "token.json"


def main():
    hidden = None
    if TOKEN_FILE.exists():
        hidden = TOKEN_FILE.with_suffix(".json.hidden_for_test")
        TOKEN_FILE.rename(hidden)
        print(f"Temporarily moved {TOKEN_FILE.name} aside to simulate an ephemeral filesystem.")

    try:
        assert not TOKEN_FILE.exists(), "token.json still present - test is not isolated"
        calendars = list_calendars()
        print(
            f"OK: reconstructed credentials from env vars alone (no token.json) and listed "
            f"{len(calendars)} calendar(s)."
        )
    finally:
        if hidden is not None:
            hidden.rename(TOKEN_FILE)
            print(f"Restored {TOKEN_FILE.name}.")


if __name__ == "__main__":
    main()
