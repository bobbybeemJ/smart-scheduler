"""Sanity check: confirms OAuth works end to end by listing your real calendars, via the real
app.calendar_client module (no duplicated auth logic - see app/calendar_client/auth.py).
Run scripts/oauth_bootstrap.py first to populate .env. Run as: python -m scripts.sanity.check_oauth_list_calendars
"""

from dotenv import load_dotenv

load_dotenv()

from app.calendar_client.client import list_calendars  # noqa: E402


def main():
    calendars = list_calendars()
    print(f"Found {len(calendars)} calendar(s):")
    for cal in calendars:
        print(f"  - {cal.get('summary')} ({cal.get('id')})")


if __name__ == "__main__":
    main()
