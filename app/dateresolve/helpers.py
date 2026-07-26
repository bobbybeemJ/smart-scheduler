"""Pure deterministic date/time arithmetic. No LLM, no network - every function here is a
plain function of its inputs, which is what makes the hard cases (last weekday of month,
business-hour clamping) reliable instead of hallucination-prone."""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta

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


_WEEKDAY_PREFIX_RE = re.compile(r"^(next|this|on|coming)\s+", re.IGNORECASE)
_TIME_TOKEN_RE = re.compile(r"\b\d{1,2}(:\d{2})?\s*(am|pm)\b|\b\d{1,2}:\d{2}\b", re.IGNORECASE)


def try_parse_weekday_only(now: dt.datetime, phrase: str) -> Optional[dt.date]:
    """Handles "Monday", "next Monday", "this Monday", "on Monday" via our own deterministic
    weekday arithmetic. dateparser is empirically unreliable on the "next <weekday>" phrasing
    (observed returning None for it), so bare weekday references never go through dateparser."""
    candidate = _WEEKDAY_PREFIX_RE.sub("", phrase).strip()
    try:
        target_weekday = weekday_index(candidate)
    except ValueError:
        return None
    days_ahead = (target_weekday - now.weekday()) % 7
    return now.date() + dt.timedelta(days=days_ahead)


def phrase_has_explicit_time(phrase: str) -> bool:
    return bool(_TIME_TOKEN_RE.search(phrase))


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


def last_weekday_of_month(reference: dt.datetime) -> dt.date:
    """Last Mon-Fri day of `reference`'s month (walks back from the last calendar day)."""
    first_of_next_month = (reference.replace(day=1) + relativedelta(months=1)).date()
    last_day = first_of_next_month - dt.timedelta(days=1)
    while last_day.weekday() >= 5:  # Saturday=5, Sunday=6
        last_day -= dt.timedelta(days=1)
    return last_day


def business_hours_window(
    day: dt.date,
    start_hour: int = DEFAULT_BUSINESS_START_HOUR,
    end_hour: int = DEFAULT_BUSINESS_END_HOUR,
) -> tuple[dt.datetime, dt.datetime]:
    start = dt.datetime.combine(day, dt.time(hour=start_hour))
    end = dt.datetime.combine(day, dt.time(hour=end_hour))
    return start, end


def next_week_range(now: dt.datetime) -> tuple[dt.date, dt.date]:
    """Next calendar week's Monday..Friday, relative to `now`."""
    days_until_monday = (7 - now.weekday()) % 7 or 7
    next_monday = (now + dt.timedelta(days=days_until_monday)).date()
    next_friday = next_monday + dt.timedelta(days=4)
    return next_monday, next_friday


def this_week_range(now: dt.datetime) -> tuple[dt.date, dt.date]:
    """This calendar week's remaining weekdays: from today through Friday. Found missing (the
    schema accepted "this_week" as a valid value, but the resolver only ever implemented
    "next_week") via testing a real "sometime this week" phrase against the live deployed
    service - real Gemini extracted it perfectly, but resolving it failed. If today is already
    Saturday/Sunday, returns a same-day no-op range that legitimately yields zero candidates,
    which slot_finder's widen-on-empty fallback already handles gracefully."""
    today = now.date()
    if now.weekday() > 4:  # Saturday=5, Sunday=6 - no weekdays left this week
        return today, today
    this_friday = today + dt.timedelta(days=4 - now.weekday())
    return today, this_friday


def time_bounds_for_preference(preference: Optional[str]) -> tuple[int, int]:
    """(earliest_hour, latest_hour) floor/ceiling for a stated vague preference."""
    if preference == "not_too_early":
        return 10, 18
    if preference == "not_too_late":
        return 9, 15
    return DEFAULT_BUSINESS_START_HOUR, DEFAULT_BUSINESS_END_HOUR
