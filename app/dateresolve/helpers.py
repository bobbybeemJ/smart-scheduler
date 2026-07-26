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
    day_part = day_part_in_phrase(phrase)
    if day_part is None:
        return phrase
    return re.sub(day_part, "", phrase, flags=re.IGNORECASE).strip()


_WEEKDAY_PREFIX_RE = re.compile(r"^(next|this|coming)?\s*(week)?\s*(on)?\s*", re.IGNORECASE)
_TIME_TOKEN_RE = re.compile(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)


def try_parse_weekday_only(now: dt.datetime, phrase: str) -> Optional[dt.date]:
    """Handles "Monday", "next Monday", "this Monday", "on Monday", and "next week on Monday"
    (a real phrasing found via real Gemini for "next week on Tuesday" - dateparser flatly fails
    to parse that exact wording, returning None) via our own deterministic weekday arithmetic.
    dateparser is also empirically unreliable on plain "next <weekday>" phrasing (observed
    returning None for it), so bare weekday references never go through dateparser at all."""
    candidate = _WEEKDAY_PREFIX_RE.sub("", phrase).strip()
    try:
        target_weekday = weekday_index(candidate)
    except ValueError:
        return None
    days_ahead = (target_weekday - now.weekday()) % 7
    return now.date() + dt.timedelta(days=days_ahead)


def phrase_has_explicit_time(phrase: str) -> bool:
    return bool(_TIME_TOKEN_RE.search(phrase))


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
