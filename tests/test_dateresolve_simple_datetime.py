"""Bonus scenario (not one of the 6 official hard cases, added after the user asked to broaden
coverage beyond them - see app/schemas.py's SimpleDateTime): "Tuesday at 2pm", "tomorrow
morning", "next Monday" - the most common phrasing a real user actually says. Also locks in two
real dateparser bugs found during manual verification (failing on "next <weekday>" phrasing and
combined day-part phrases like "tomorrow morning")."""

import datetime as dt

from app.dateresolve.resolver import resolve
from app.schemas import SimpleDateTime

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def test_exact_time_stated():
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="Tuesday at 2pm"), NOW)
    window = constraints.search_windows[0]
    assert window.start == dt.datetime(2026, 7, 28, 14, 0)  # next Tuesday


def test_day_part_with_no_exact_time():
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="tomorrow morning"), NOW)
    window = constraints.search_windows[0]
    assert window.start == dt.datetime(2026, 7, 23, 9, 0)
    assert window.end == dt.datetime(2026, 7, 23, 12, 0)


def test_next_weekday_with_no_exact_time():
    """Regression test: dateparser itself returns None for "next Monday" (confirmed via a real
    call during Phase 1 development) - this must route through the deterministic weekday
    helper instead, not dateparser, for phrases prefixed with next/this/on."""
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="next Monday"), NOW)
    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 7, 27)


def test_bare_weekday_without_prefix():
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="Friday"), NOW)
    assert constraints.search_windows[0].start.date() == dt.date(2026, 7, 24)


def test_relative_phrase_falls_back_to_dateparser():
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="in 3 days"), NOW)
    assert constraints.search_windows[0].start.date() == dt.date(2026, 7, 25)
