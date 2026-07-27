"""Deterministic date/time resolution. Every function here is a pure function of
(expression, now, calendar lookups) - no LLM call happens anywhere in this module."""

from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

import dateparser

from app.dateresolve import helpers
from app.schemas import (
    CalendarArithmetic,
    ContextualReference,
    DeadlineBefore,
    DurationUpdate,
    DynamicBuffer,
    EventRelative,
    RelativeRangeWithExclusions,
    ResolvedConstraints,
    SimpleDateTime,
    TemporalExpression,
    TimeWindow,
    WeekPosition,
)

# event_name -> {"start": datetime, "end": datetime} or None if not found
CalendarLookupFn = Callable[[str], Optional[dict]]
# date -> last event of that day ({"start":..., "end":...}) or None
LastMeetingLookupFn = Callable[[dt.date], Optional[dict]]


class UnresolvedReferenceError(Exception):
    """Raised when a named event or contextual reference can't be found. The dialogue layer
    (Phase 3) should turn this into a clarifying question, never a crash or a guessed date."""


class MissingDurationError(Exception):
    """Raised when duration_minutes wasn't stated (and hasn't been filled in from session state
    yet). This is what turns "info is missing" into a clarifying question upstream, instead of
    the LLM inventing a plausible-looking default - confirmed via a real Gemini call that it will
    otherwise guess a duration (e.g. 30) out of nowhere when the schema forces a non-null int."""


class PastDateError(Exception):
    """Raised when a resolved search window is entirely in the past. Found via testing "last
    week" and "yesterday" against real Gemini: both extracted cleanly, and both resolved to a
    genuine past datetime with NO error at all - deadline_before/event_relative/etc. never
    checked whether "now" or "event end + offset" could land before "now". This check is
    deliberately centralized here in resolve() rather than duplicated in every _resolve_*
    function, so it protects every current and future case uniformly with one guard, not one
    per case."""


class MissingAnchorTimeError(Exception):
    """Raised when DeadlineBefore.anchor_time wasn't stated. Found via testing real Gemini on
    "before I leave for my trip on Friday" (no time given at all): the model didn't leave the
    field blank or ask for help - it invented "18:00" from nothing, a direct violation of the
    "never invent a date/time" rule. Same fix shape as MissingDurationError above."""


def resolve(
    expr: TemporalExpression,
    now: dt.datetime,
    find_event: Optional[CalendarLookupFn] = None,
    find_last_meeting: Optional[LastMeetingLookupFn] = None,
) -> ResolvedConstraints:
    if not isinstance(expr, (ContextualReference, DurationUpdate)) and getattr(expr, "duration_minutes", None) is None:
        raise MissingDurationError(
            "duration_minutes is not known yet - ask the user how long the meeting should be "
            "before calling resolve()."
        )

    constraints = _dispatch(expr, now, find_event, find_last_meeting)

    if all(window.end <= now for window in constraints.search_windows):
        raise PastDateError(
            f"Resolved search window(s) are entirely in the past relative to now ({now}): "
            f"{constraints.search_windows} - can't schedule a new meeting in the past."
        )

    return constraints


def _dispatch(
    expr: TemporalExpression,
    now: dt.datetime,
    find_event: Optional[CalendarLookupFn],
    find_last_meeting: Optional[LastMeetingLookupFn],
) -> ResolvedConstraints:
    if isinstance(expr, DeadlineBefore):
        return _resolve_deadline_before(expr, now)
    if isinstance(expr, EventRelative):
        return _resolve_event_relative(expr, find_event)
    if isinstance(expr, CalendarArithmetic):
        return _resolve_calendar_arithmetic(expr, now)
    if isinstance(expr, RelativeRangeWithExclusions):
        return _resolve_relative_range(expr, now)
    if isinstance(expr, DynamicBuffer):
        return _resolve_dynamic_buffer(expr, now, find_last_meeting, find_event)
    if isinstance(expr, SimpleDateTime):
        return _resolve_simple_datetime(expr, now)
    if isinstance(expr, ContextualReference):
        raise ValueError(
            "ContextualReference has no standalone window. Call resolve_contextual_duration() "
            "first, then resolve() the other constraint already established in session state."
        )
    if isinstance(expr, DurationUpdate):
        raise ValueError(
            "DurationUpdate has no standalone window. The dialogue layer must merge it into the "
            "existing established expression and call resolve() on that, not on this directly."
        )
    raise ValueError(f"Unhandled temporal expression: {expr!r}")


def resolve_contextual_duration(expr: ContextualReference, known_defaults: dict[str, int]) -> int:
    """"our usual sync-up" -> a remembered duration. `known_defaults` comes from SessionState
    (Phase 3), not the LLM - the model only ever says which reference the user meant."""
    key = expr.reference.strip().lower()
    for remembered_key, minutes in known_defaults.items():
        if remembered_key in key or key in remembered_key:
            return minutes
    raise UnresolvedReferenceError(f"No remembered duration for {expr.reference!r}")


def _resolve_deadline_before(expr: DeadlineBefore, now: dt.datetime) -> ResolvedConstraints:
    if expr.anchor_time is None:
        raise MissingAnchorTimeError(
            "anchor_time is not known yet - ask the user what time the deadline is before "
            "calling resolve()."
        )
    anchor = helpers.next_occurrence(now, expr.anchor_weekday, expr.anchor_time)
    deadline = anchor - dt.timedelta(minutes=expr.buffer_minutes)
    earliest_hour = helpers.parse_hhmm(expr.earliest_time)[0] if expr.earliest_time else None
    # Found via real usage: with no weekend exclusion at all, a multi-day window that happened
    # to start on a weekend (e.g. deadline stated for a weekday next week, "now" already a
    # Saturday/Sunday) proposed candidates ON that weekend - never asked for. Exclude both
    # weekend days EXCEPT the anchor's own weekday, so "before my flight Saturday" still allows
    # Saturday itself (the one day that's actually the point of the request).
    anchor_weekday_index = helpers.weekday_index(expr.anchor_weekday)
    excluded_weekdays = [weekday for weekday in (5, 6) if weekday != anchor_weekday_index]
    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=now, end=deadline)],
        hard_deadline=deadline,
        earliest_hour=earliest_hour,
        excluded_weekdays=excluded_weekdays,
    )


def _resolve_event_relative(expr: EventRelative, find_event: Optional[CalendarLookupFn]) -> ResolvedConstraints:
    if find_event is None:
        raise ValueError("find_event lookup is required to resolve an event_relative expression")
    event = find_event(expr.event_name)
    if event is None:
        raise UnresolvedReferenceError(f"Could not find an event named {expr.event_name!r} on the calendar")

    anchor_end = event["end"]
    window_start_date = (anchor_end + dt.timedelta(days=expr.offset_days_min)).date()
    window_end_date = (anchor_end + dt.timedelta(days=expr.offset_days_max)).date()
    start, _ = helpers.business_hours_window(window_start_date)
    _, end = helpers.business_hours_window(window_end_date)
    earliest_hour = helpers.parse_hhmm(expr.earliest_time)[0] if expr.earliest_time else None

    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=start, end=end)],
        earliest_hour=earliest_hour,
    )


def _resolve_calendar_arithmetic(expr: CalendarArithmetic, now: dt.datetime) -> ResolvedConstraints:
    day = helpers.nth_weekday_of_month(now, expr.ordinal.value, expr.day_type, expr.month_offset)
    start, end = helpers.business_hours_window(day)
    return ResolvedConstraints(duration_minutes=expr.duration_minutes, search_windows=[TimeWindow(start=start, end=end)])


def _resolve_relative_range(expr: RelativeRangeWithExclusions, now: dt.datetime) -> ResolvedConstraints:
    start_date, end_date = helpers.week_range(now, expr.week_offset)

    if expr.week_position == WeekPosition.LATE_IN_RANGE:
        start_date = max(start_date, end_date - dt.timedelta(days=1))  # Thu-Fri
    elif expr.week_position == WeekPosition.EARLY_IN_RANGE:
        end_date = min(end_date, start_date + dt.timedelta(days=1))  # Mon-Tue

    preference_value = expr.time_preference.value if expr.time_preference else None
    earliest_hour, latest_hour = helpers.time_bounds_for_preference(preference_value)
    start = dt.datetime.combine(start_date, dt.time(hour=earliest_hour))
    if start_date == now.date():
        # "this week" can start today - never propose a candidate window starting in the past.
        start = max(start, now)
    end = dt.datetime.combine(end_date, dt.time(hour=latest_hour))
    excluded = [helpers.weekday_index(day_name) for day_name in expr.exclude_weekdays]

    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=start, end=end)],
        excluded_weekdays=excluded,
        time_preference=expr.time_preference,
    )


def _resolve_dynamic_buffer(
    expr: DynamicBuffer,
    now: dt.datetime,
    find_last_meeting: Optional[LastMeetingLookupFn],
    find_event: Optional[CalendarLookupFn] = None,
) -> ResolvedConstraints:
    today = now.date()
    stated_earliest: Optional[dt.datetime] = None
    if expr.earliest_time is not None:
        stated_hour, stated_minute = helpers.parse_hhmm(expr.earliest_time)
        stated_earliest = dt.datetime.combine(today, dt.time(hour=stated_hour, minute=stated_minute))

    if expr.buffer_source == "named_event":
        if find_event is None:
            raise ValueError("find_event lookup is required to resolve a named_event dynamic_buffer expression")
        if not expr.reference_event_name:
            raise ValueError("reference_event_name is required when buffer_source is 'named_event'")
        anchor_event = find_event(expr.reference_event_name)
        if anchor_event is None:
            raise UnresolvedReferenceError(f"Could not find an event named {expr.reference_event_name!r} on the calendar")
    else:
        if find_last_meeting is None:
            raise ValueError("find_last_meeting lookup is required to resolve a dynamic_buffer expression")
        anchor_event = find_last_meeting(today)

    if anchor_event is not None:
        buffer_earliest = anchor_event["end"] + dt.timedelta(minutes=expr.buffer_minutes)
        earliest_start = max(stated_earliest, buffer_earliest) if stated_earliest is not None else buffer_earliest
    elif stated_earliest is not None:
        earliest_start = stated_earliest
    else:
        raise UnresolvedReferenceError(
            "dynamic_buffer has neither a stated clock time nor a resolvable anchor event to buffer from"
        )

    end_of_evening = dt.datetime.combine(today, dt.time(hour=helpers.DEFAULT_EVENING_END_HOUR))
    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=earliest_start, end=end_of_evening)],
        earliest_start=earliest_start,
    )


_MAX_RAW_PHRASE_WORDS = 12
_MAX_RAW_PHRASE_CHARS = 60


def _resolve_simple_datetime(expr: SimpleDateTime, now: dt.datetime) -> ResolvedConstraints:
    """Broken into the four sequential named steps below - grew into one long function through
    several independent rounds of real-usage bug fixes (o'clock, day-parts, weekday prefixes,
    AM/PM), each individually well-justified but cumulatively hard to hold in your head as one
    block. Splitting by responsibility (not behavior change) keeps it that way."""
    if expr.raw_phrase is None:
        raise UnresolvedReferenceError("No day/time stated yet")
    _reject_if_implausibly_long(expr.raw_phrase)

    raw_phrase = helpers.normalize_oclock(expr.raw_phrase)
    day_part = helpers.day_part_in_phrase(raw_phrase)
    remainder = helpers.strip_day_part(raw_phrase) if day_part else raw_phrase

    base_date = helpers.try_parse_weekday_only(now, remainder)
    explicit_time: Optional[tuple[int, int]] = None
    if base_date is None:
        base_date, explicit_time = _parse_via_dateparser(remainder, now, day_part, expr.raw_phrase)

    start, end = _compute_window(base_date, explicit_time, day_part)
    # exact_start only when a precise moment was actually stated (not a bare day or day-part) -
    # signals the dialogue layer to present just that one slot if it's free, rather than ranking
    # it alongside nearby alternatives the user never asked for.
    exact_start = start if explicit_time is not None else None
    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=start, end=end)],
        exact_start=exact_start,
    )


def _reject_if_implausibly_long(raw_phrase: str) -> None:
    """Defends against a real, if intermittent, failure mode of the free-tier LLM: instead of a
    short clean phrase like "next Wednesday at 3pm," it occasionally emits its own leaked
    reasoning into raw_phrase instead ("...removed duration text as required rule 2 says
    raw_phrase must not contain..." - an actual observed sample). A genuine date/time phrase is
    never this long, and dateparser might otherwise latch onto some date-like fragment buried in
    all that noise and resolve to an unpredictable date instead of failing cleanly - reject
    outright and ask again rather than gamble on what dateparser does with it.

    Checks BOTH word count and raw character length - word count alone missed a real observed
    case: the model hallucinated a UUID and an ISO timestamp appended after a clean phrase
    ("Friday afternoon b20451cf-41ee-4171-8bc5-01e4ecbe567a 2026-03-31 03:00:23.490793 UTC"),
    which is only 6 whitespace-separated "words" (hyphen/colon-joined, not space-joined) despite
    being obvious garbage - and is exactly the dangerous shape, since dateparser is good at
    parsing the ISO-looking fragments such garbage tends to contain, risking a confidently wrong
    date instead of a clean failure."""
    if len(raw_phrase.split()) > _MAX_RAW_PHRASE_WORDS or len(raw_phrase) > _MAX_RAW_PHRASE_CHARS:
        raise UnresolvedReferenceError(f"raw_phrase is implausibly long, rejecting rather than guessing: {raw_phrase!r}")


def _parse_via_dateparser(
    remainder: str, now: dt.datetime, day_part: Optional[str], original_phrase: str
) -> tuple[dt.date, Optional[tuple[int, int]]]:
    """Reached only once try_parse_weekday_only has ruled out a bare weekday reference. Handles
    three separate dateparser quirks found via real usage, each sitting between "call dateparser"
    and "trust its answer":
    1. dateparser fails on a "next/this/coming <weekday>" prefix combined with anything trailing
       it (confirmed: "next Wednesday 3:00" -> None, "Wednesday 3:00" parses fine) - strip that
       prefix first, but only when a weekday is actually named.
    2. dateparser can confidently return a date that isn't even the weekday the user named (e.g.
       "Wednesday 6" -> a Saturday) - reject rather than silently trust a wrong guess.
    3. An hour stated as bare "3 o'clock" (no am/pm) is ambiguous - normalize_oclock already
       turned it into "3:00", which dateparser/dt.time both default to AM. If a day-part word is
       also present ("in the afternoon"), it's the only thing disambiguating that hour - use it,
       rather than silently losing the "afternoon" qualifier."""
    stated_weekday = helpers.extract_stated_weekday(remainder)
    dateparser_input = helpers.strip_weekday_prefix(remainder) if stated_weekday is not None else remainder
    parsed = dateparser.parse(dateparser_input, settings={"RELATIVE_BASE": now, "PREFER_DATES_FROM": "future"})
    if parsed is None:
        raise UnresolvedReferenceError(f"Could not parse date/time phrase: {original_phrase!r}")

    # An explicit "next/this week" qualifier changes which week's occurrence of the stated
    # weekday is meant (see helpers.week_qualifier_offset's docstring for the bug this closes:
    # "next week on Monday" was silently resolving to the SAME date as bare "Monday" whenever
    # dateparser's own guess happened to land on the right weekday, e.g. today itself being
    # Monday) - computed deterministically instead of trusting dateparser's date, which never
    # saw the qualifier at all (stripped out before the dateparser call above).
    week_offset = helpers.week_qualifier_offset(remainder) if stated_weekday is not None else None
    if week_offset is not None:
        base_date = helpers.weekday_in_week_offset(now, week_offset, stated_weekday)
    else:
        base_date = parsed.date()
        if stated_weekday is not None and base_date.weekday() != stated_weekday:
            raise UnresolvedReferenceError(
                f"Could not reliably parse {original_phrase!r} - resolved to {base_date} "
                f"({base_date.strftime('%A')}), which doesn't match the stated weekday"
            )

    explicit_time: Optional[tuple[int, int]] = None
    # dateparser copies now's time-of-day when the phrase has no explicit time - so a differing
    # time (or an explicit am/pm/colon token in the text) means one was stated.
    if helpers.phrase_has_explicit_time(remainder) or (parsed.hour, parsed.minute) != (now.hour, now.minute):
        explicit_time = (parsed.hour, parsed.minute)
        if day_part is not None and not helpers.has_explicit_ampm(original_phrase):
            hour, minute = explicit_time
            if day_part in ("afternoon", "evening", "night") and 1 <= hour <= 11:
                explicit_time = (hour + 12, minute)

    return base_date, explicit_time


def _compute_window(
    base_date: dt.date, explicit_time: Optional[tuple[int, int]], day_part: Optional[str]
) -> tuple[dt.datetime, dt.datetime]:
    """An explicit time wins over a day-part word when both are present - found via real usage:
    "3 o'clock in the afternoon" states both for the same moment (not two constraints), and a
    generic noon-5pm day-part window is a worse answer than the specific 3pm actually stated."""
    if explicit_time is not None:
        start = dt.datetime.combine(base_date, dt.time(hour=explicit_time[0], minute=explicit_time[1]))
        _, end = helpers.business_hours_window(base_date)
        if end <= start:
            end = start + dt.timedelta(hours=1)
        return start, end
    if day_part is not None:
        start_hour, end_hour = helpers.DAY_PART_WINDOWS[day_part]
        return helpers.business_hours_window(base_date, start_hour, end_hour)
    return helpers.business_hours_window(base_date)
