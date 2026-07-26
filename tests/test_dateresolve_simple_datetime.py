"""Bonus scenario (not one of the 6 official hard cases, added after the user asked to broaden
coverage beyond them - see app/schemas.py's SimpleDateTime): "Tuesday at 2pm", "tomorrow
morning", "next Monday" - the most common phrasing a real user actually says. Also locks in two
real dateparser bugs found during manual verification (failing on "next <weekday>" phrasing and
combined day-part phrases like "tomorrow morning")."""

import datetime as dt

from app.dateresolve.resolver import UnresolvedReferenceError, resolve
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


def test_next_week_on_weekday_phrasing():
    """"next week on Tuesday" - found via real usage: real Gemini correctly classifies this as
    simple_datetime (not a range - see relative_range_with_exclusions's docstring), but
    dateparser flatly fails to parse this exact wording (returns None), so it must route through
    the same deterministic weekday helper as "next Tuesday" instead."""
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="next week on Tuesday"), NOW)
    assert constraints.search_windows[0].start.date() == dt.date(2026, 7, 28)


def test_relative_phrase_falls_back_to_dateparser():
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="in 3 days"), NOW)
    assert constraints.search_windows[0].start.date() == dt.date(2026, 7, 25)


def test_weekday_mismatch_from_dateparser_is_rejected_not_silently_trusted():
    """"Wednesday 6" - found via real usage (likely a garbled "Wednesday at 6"): dateparser
    confidently resolved this to a date that isn't even a Wednesday (a Tuesday, a year later, in
    this fixed-NOW scenario), and the system booked a slot on it with zero error. A stated
    weekday that the resolved date doesn't actually fall on is a strong signal the parse is
    wrong, not that the user meant something unusual - reject it and ask again instead."""
    try:
        resolve(SimpleDateTime(duration_minutes=30, raw_phrase="Wednesday 6"), NOW)
        assert False, "expected UnresolvedReferenceError - resolved date isn't a Wednesday"
    except UnresolvedReferenceError:
        pass
