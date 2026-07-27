"""Pure deterministic date/time arithmetic. No LLM, no network - every function here is a
plain function of its inputs, which is what makes the hard cases (last weekday of month,
business-hour clamping) reliable instead of hallucination-prone."""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta

from app.schemas import DAY_TYPE_WEEKDAY

WEEKDAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

DEFAULT_BUSINESS_START_HOUR = 9
DEFAULT_BUSINESS_END_HOUR = 17
DEFAULT_EVENING_END_HOUR = 22

# Deterministic day-part -> (start_hour, end_hour) mapping, checked before falling back to
# dateparser's fuzzier time inference - keeps "morning"/"afternoon"/"evening" reliable.
DAY_PART_WINDOWS: dict[str, tuple[int, int]] = {
    "morning": (9, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (20, 23),
}


def day_part_in_phrase(phrase: str) -> Optional[str]:
    lowered = phrase.lower()
    return next((part for part in DAY_PART_WINDOWS if part in lowered), None)


def strip_day_part(phrase: str) -> str:
    """Removes the day-part word AND any surrounding "in (the)"/"at (the)" preposition as one
    unit - found via real usage: "next Thursday in the morning" only had the bare word "morning"
    stripped, leaving "next Thursday in the" dangling. That trailing "in the" broke the
    weekday-only fast parser (which requires the remainder to be just the weekday name), forcing
    a fallback to dateparser on a phrase dateparser couldn't handle either."""
    day_part = day_part_in_phrase(phrase)
    if day_part is None:
        return phrase
    pattern = re.compile(rf"\b(in|during|at)?\s*(the)?\s*{day_part}\b", re.IGNORECASE)
    return pattern.sub("", phrase).strip()


_WEEKDAY_PREFIX_RE = re.compile(r"^(next|this|coming)?\s*(week)?\s*(on)?\s*", re.IGNORECASE)
_TIME_TOKEN_RE = re.compile(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b|\b\d{1,2}\s*o'?clock\b", re.IGNORECASE)


def strip_weekday_prefix(phrase: str) -> str:
    """Removes a leading "next"/"this"/"coming"/"week"/"on" combination (in any of the
    supported orders) - dateparser is unreliable on this prefix combined with ANYTHING trailing
    it, not just a bare weekday name: confirmed directly that "next Wednesday 3:00" returns None
    while the otherwise-identical "Wednesday 3:00" (no prefix) parses correctly. Used both by
    try_parse_weekday_only (which needs a bare weekday name after stripping) and as a
    pre-processing step before handing a weekday+time phrase to dateparser."""
    return _WEEKDAY_PREFIX_RE.sub("", phrase).strip()


_WEEK_QUALIFIER_RE = re.compile(r"^(next|this|coming)\s+week\b", re.IGNORECASE)


def week_qualifier_offset(phrase: str) -> Optional[int]:
    """0 for "this week", 1 for "next"/"coming week" - an explicit calendar-week qualifier
    stated before a weekday name ("next week on Monday"), as opposed to "next Monday" alone (no
    "week" word - the other prefix shape _WEEKDAY_PREFIX_RE also strips), which means just the
    nearest occurrence of that weekday, not necessarily next calendar week. Returns None if no
    such qualifier is present, so callers fall back to nearest-occurrence arithmetic.

    Found via testing Claude vs Gemini side by side (2026-07-27): both extracted "next week on
    Tuesday at 2pm" as simple_datetime with the whole phrase as raw_phrase (correct kind choice
    per schemas.py's own rule), but resolve() was silently treating it identically to bare
    "Tuesday" - if today already IS Monday, "next week on Monday" was resolving to TODAY, not a
    week later. try_parse_weekday_only's docstring even claimed this exact phrasing was already
    handled, but the "handling" only kept dateparser from crashing on it (by never calling
    dateparser for a bare weekday), not the week-offset semantics of the qualifier itself - the
    old code below never distinguished "next week on Monday" from plain "next Monday" at all."""
    match = _WEEK_QUALIFIER_RE.match(phrase.strip())
    if match is None:
        return None
    return 0 if match.group(1).lower() == "this" else 1


def weekday_in_week_offset(now: dt.datetime, week_offset: int, target_weekday: int) -> dt.date:
    """The date of target_weekday (0=Monday) within the Mon-Sun calendar week that is
    week_offset weeks from now's calendar week (0=this week, 1=next week) - the same Mon-Sun
    week convention RelativeRangeWithExclusions.week_offset already uses elsewhere."""
    this_monday = now.date() - dt.timedelta(days=now.weekday())
    target_monday = this_monday + dt.timedelta(weeks=week_offset)
    return target_monday + dt.timedelta(days=target_weekday)


def try_parse_weekday_only(now: dt.datetime, phrase: str) -> Optional[dt.date]:
    """Handles "Monday", "next Monday", "this Monday", "on Monday", and "next week on Monday"
    via our own deterministic weekday arithmetic - dateparser is empirically unreliable on any
    of these prefixed forms (confirmed returning None for "next Wednesday 3:00" and for "next
    week on Tuesday" alike), so bare weekday references never go through dateparser at all."""
    candidate = strip_weekday_prefix(phrase)
    try:
        target_weekday = weekday_index(candidate)
    except ValueError:
        return None
    week_offset = week_qualifier_offset(phrase)
    if week_offset is not None:
        return weekday_in_week_offset(now, week_offset, target_weekday)
    days_ahead = (target_weekday - now.weekday()) % 7
    return now.date() + dt.timedelta(days=days_ahead)


def phrase_has_explicit_time(phrase: str) -> bool:
    return bool(_TIME_TOKEN_RE.search(phrase))


def extract_time_token(phrase: str) -> Optional[str]:
    """The matched clock-time substring only (e.g. "12:00 p.m." out of "book it for 12:00
    p.m."), or None if there isn't one. dateparser is unreliable on a time with filler words
    around it (confirmed: "book it for 12:00 p.m." -> None, while the isolated "12:00 p.m."
    parses fine) - the same "leading junk confuses dateparser" pattern as the weekday-prefix
    issue, just for time tokens instead of weekday names. Parse the isolated substring instead
    of the whole sentence."""
    match = _TIME_TOKEN_RE.search(phrase)
    return match.group(0) if match else None


_AMPM_RE = re.compile(r"\b(am|pm|a\.m\.|p\.m\.)\b", re.IGNORECASE)


def has_explicit_ampm(phrase: str) -> bool:
    return bool(_AMPM_RE.search(phrase))


_OCLOCK_RE = re.compile(r"\b(\d{1,2})\s*o'?clock\b", re.IGNORECASE)


def normalize_oclock(phrase: str) -> str:
    """"3 o'clock" -> "3:00" - found via real usage: dateparser cannot parse "o'clock" phrasing
    at all (confirmed directly - returns None even for "next Wednesday at 3 o'clock"), despite
    handling the equivalent numeric "3:00" just fine. "N o'clock" is a very natural way to say a
    time out loud, so STT transcripts containing it are common, not an edge case."""
    return _OCLOCK_RE.sub(lambda m: f"{m.group(1)}:00", phrase)


_WEEKDAY_NAME_RE = re.compile(r"\b(" + "|".join(WEEKDAY_NAMES) + r")\b", re.IGNORECASE)


def extract_stated_weekday(phrase: str) -> Optional[int]:
    """The weekday index (0=Monday) of a weekday name found anywhere in the phrase, or None if
    none is mentioned. Used to sanity-check dateparser's output - found via testing real usage:
    "Wednesday 6" (an ambiguous phrase, likely a garbled "Wednesday at 6") got parsed by
    dateparser into a date that was a SATURDAY, not a Wednesday at all, and was trusted with no
    error. A stated weekday that the resolved date doesn't actually fall on is a strong signal
    dateparser guessed wrong, not that the user meant something unusual."""
    match = _WEEKDAY_NAME_RE.search(phrase)
    return WEEKDAY_NAMES.index(match.group(1).lower()) if match else None


def weekday_index(name: str) -> int:
    """"Wednesday" -> 2 (Monday=0). Raises on anything not a recognized weekday name."""
    key = name.strip().lower()
    if key not in WEEKDAY_NAMES:
        raise ValueError(f"Unrecognized weekday name: {name!r}")
    return WEEKDAY_NAMES.index(key)


def parse_hhmm(time_str: str) -> tuple[int, int]:
    """Accepts "18:00", "6 PM", "6:30pm", etc. Returns (hour_24, minute)."""
    try:
        hour_str, minute_str = time_str.split(":")
        return int(hour_str), int(minute_str)
    except ValueError:
        parsed = date_parser.parse(time_str)
        return parsed.hour, parsed.minute


def next_occurrence(now: dt.datetime, weekday_name: str, time_str: str) -> dt.datetime:
    """Next future datetime matching the given weekday name + time-of-day, strictly after `now`."""
    target_weekday = weekday_index(weekday_name)
    hour, minute = parse_hhmm(time_str)
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (target_weekday - now.weekday()) % 7
    candidate += dt.timedelta(days=days_ahead)
    if candidate <= now:
        candidate += dt.timedelta(days=7)
    return candidate


_ORDINAL_INDEX = {"first": 0, "second": 1, "third": 2, "fourth": 3, "fifth": 4}


def nth_weekday_of_month(reference: dt.datetime, ordinal: str, day_type: str, month_offset: int = 0) -> dt.date:
    """The general form behind "last weekday of this month", "first weekday of next month", "the
    second Tuesday of next month", "the last Friday of the month" - one function covering the
    full (ordinal x day_type x month) space, replacing what used to be a separate hardcoded case
    per phrase (last_weekday_of_month(), then first_weekday_of_month() bolted on next to it,
    which was heading toward needing a new function for every new phrase forever).

    day_type == "weekday" means any Mon-Fri business day (the assignment brief's own "last
    weekday of the month" example); any other value is a specific weekday name, e.g. "friday".
    Raises ValueError if the target month doesn't have that many occurrences (e.g. the 5th Monday
    of a month that only has four) - a real possibility now that FIFTH is a reachable ordinal,
    not a made-up edge case."""
    target_month_start = (reference.replace(day=1) + relativedelta(months=month_offset)).date()
    next_month_start = target_month_start + relativedelta(months=1)
    target_weekday = None if day_type == DAY_TYPE_WEEKDAY else weekday_index(day_type)

    candidates = []
    day = target_month_start
    while day < next_month_start:
        if (target_weekday is None and day.weekday() < 5) or day.weekday() == target_weekday:
            candidates.append(day)
        day += dt.timedelta(days=1)

    if ordinal == "last":
        return candidates[-1]
    index = _ORDINAL_INDEX[ordinal]
    if index >= len(candidates):
        raise ValueError(
            f"{target_month_start.strftime('%B %Y')} doesn't have a {ordinal!r} {day_type} "
            f"(only {len(candidates)} found)"
        )
    return candidates[index]


def business_hours_window(
    day: dt.date,
    start_hour: int = DEFAULT_BUSINESS_START_HOUR,
    end_hour: int = DEFAULT_BUSINESS_END_HOUR,
) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(hour=start_hour))
    end = dt.datetime.combine(day, dt.time(hour=end_hour))
    return start, end


def week_range(now: dt.datetime, offset: int) -> tuple[dt.date, dt.date]:
    """The Monday..Friday of the calendar week `offset` weeks from now's current week:
    0 = this week, 1 = next week, 2 = the week after next / "two weeks from now", -1 = last
    week, etc. Replaced separate next_week_range()/this_week_range() functions with one signed-
    offset version - found via testing that Gemini kept abandoning a two-value
    Literal["this_week","next_week"] enum for anything slightly different ("the week after
    next") and silently falling back to a weaker catch-all case instead, losing
    exclude_weekdays/time_preference in the process.

    When offset == 0 and today is a weekday, the range starts today (not Monday) since a
    candidate can never be proposed in the past; if today is already Sat/Sun for offset == 0,
    returns a same-day no-op range (yields zero candidates, which slot_finder's widen-on-empty
    fallback handles gracefully). Negative offsets (past weeks) are intentionally still computed
    here rather than rejected - resolve()'s universal past-date safety net rejects an
    entirely-past result centrally for every case, not just this one, so this function stays a
    plain, honest calculation."""
    this_monday = now.date() - dt.timedelta(days=now.weekday())
    target_monday = this_monday + dt.timedelta(weeks=offset)
    target_friday = target_monday + dt.timedelta(days=4)

    if offset == 0:
        if now.weekday() > 4:  # Saturday=5, Sunday=6 - no weekdays left this week
            return now.date(), now.date()
        return now.date(), target_friday

    return target_monday, target_friday


def time_bounds_for_preference(preference: Optional[str]) -> tuple[int, int]:
    """(earliest_hour, latest_hour) floor/ceiling for a stated vague preference."""
    if preference == "not_too_early":
        return 10, 18
    if preference == "not_too_late":
        return 9, 15
    return DEFAULT_BUSINESS_START_HOUR, DEFAULT_BUSINESS_END_HOUR
