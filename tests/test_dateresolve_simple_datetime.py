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
    call) - this must route through the deterministic weekday helper instead, not dateparser,
    for phrases prefixed with next/this/on."""
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


def test_next_week_qualifier_actually_shifts_a_week_not_just_nearest_occurrence():
    """Regression test for a bug found via Claude-vs-Gemini comparison testing (2026-07-27),
    provider-independent (purely in the deterministic resolver): the "next week" qualifier was
    being silently dropped whenever the nearest future occurrence of the stated weekday already
    happened to fall in what colloquially reads as "next week" - "next week on Tuesday" and bare
    "Tuesday" resolved identically. test_next_week_on_weekday_phrasing above didn't catch this
    because NOW's weekday (Wednesday) happens to make the nearest-Tuesday and next-week's-Tuesday
    dates coincide. This test picks a target weekday (Friday) that HASN'T happened yet this week
    relative to NOW (Wednesday) - so nearest-occurrence (this Friday, July 24) and next-week's
    occurrence (July 31) genuinely differ, which is what actually exercises the fix."""
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="next week on Friday"), NOW)
    assert constraints.search_windows[0].start.date() == dt.date(2026, 7, 31)  # NOT July 24


def test_this_week_qualifier_stays_within_the_current_week():
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="this week on Friday"), NOW)
    assert constraints.search_windows[0].start.date() == dt.date(2026, 7, 24)


def test_next_week_qualifier_with_explicit_time_still_shifts_a_week():
    """Same bug as test_next_week_qualifier_actually_shifts_a_week_not_just_nearest_occurrence,
    but with an explicit time attached - this phrasing routes through _parse_via_dateparser
    (dateparser is used for the TIME portion) rather than try_parse_weekday_only, a different
    code path that needed the identical fix."""
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="next week on Friday at 2pm"), NOW)
    window = constraints.search_windows[0]
    assert window.start == dt.datetime(2026, 7, 31, 14, 0)  # NOT July 24


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


def test_oclock_phrasing_is_parsed_not_rejected():
    """"next Wednesday 3 o'clock" - found via real usage: dateparser cannot parse "o'clock"
    phrasing at all (confirmed directly - returns None), even though it handles the equivalent
    numeric "3:00" without any trouble. "N o'clock" is a very natural way to say a time out loud,
    so this isn't an edge case - normalizing it before dateparser sees it fixes the whole class."""
    constraints = resolve(SimpleDateTime(duration_minutes=45, raw_phrase="next Wednesday 3 o'clock"), NOW)
    window = constraints.search_windows[0]
    assert window.start == dt.datetime(2026, 7, 29, 3, 0)


def test_implausibly_long_raw_phrase_is_rejected_not_fed_to_dateparser():
    """Found via real usage: the free-tier LLM occasionally (confirmed reproducible roughly 1 in
    4 tries on one specific phrase) leaks its own reasoning into raw_phrase instead of a short
    clean date/time phrase. A genuine date/time phrase is never this long - reject it outright
    rather than risk dateparser latching onto some fragment inside all that noise."""
    garbage = (
        "next Wednesday at 3pm skyline format or similar text handled by dateparser next "
        "Wednesday at 3pm o'clock in the afternoon removed duration text as required rule 2 says"
    )
    try:
        resolve(SimpleDateTime(duration_minutes=45, raw_phrase=garbage), NOW)
        assert False, "expected UnresolvedReferenceError for an implausibly long raw_phrase"
    except UnresolvedReferenceError:
        pass


def test_explicit_time_wins_over_day_part_but_day_part_still_disambiguates_ampm():
    """"3 o'clock in the afternoon" - found via real usage in two stages: first, day_part being
    checked before explicit_time meant the specific "3pm" was discarded in favor of a generic
    noon-start afternoon window. Fixing that priority order then exposed a second bug: an
    ambiguous bare hour like "3 o'clock" (no am/pm marker) defaults to AM once normalized to
    "3:00," silently dropping the "afternoon" qualifier that was the only thing disambiguating
    it. Both need to hold at once: the exact hour wins, AND the day-part word still resolves
    whether that hour means AM or PM when nothing else says so."""
    constraints = resolve(SimpleDateTime(duration_minutes=45, raw_phrase="next Wednesday at 3 o'clock in the afternoon"), NOW)
    window = constraints.search_windows[0]
    assert window.start == dt.datetime(2026, 7, 29, 15, 0)  # 3 PM, not 3 AM or a generic noon start


def test_day_part_with_trailing_preposition_junk():
    """"next Thursday in the morning" - found via real usage: only the bare word "morning" was
    being stripped, leaving "next Thursday in the" dangling, which broke the deterministic
    weekday-only parser (needs the remainder to be just the weekday name) and fell through to
    dateparser, which also couldn't handle the leftover "in the" suffix."""
    constraints = resolve(SimpleDateTime(duration_minutes=30, raw_phrase="next Thursday in the morning"), NOW)
    window = constraints.search_windows[0]
    assert window.start == dt.datetime(2026, 7, 23, 9, 0)
    assert window.end == dt.datetime(2026, 7, 23, 12, 0)
