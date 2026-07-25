"""Deterministic date/time resolution. Every function here is a pure function of
(expression, now, calendar lookups) - no LLM call happens anywhere in this module."""

from __future__ import annotations

import datetime as dt
from typing import Callable, Optional

from app.dateresolve import helpers
from app.schemas import (
    CalendarArithmetic,
    CalendarArithmeticExpr,
    ContextualReference,
    DeadlineBefore,
    DynamicBuffer,
    EventRelative,
    RelativeRangeWithExclusions,
    ResolvedConstraints,
    TemporalExpression,
    TimeWindow,
)

# event_name -> {"start": datetime, "end": datetime} or None if not found
CalendarLookupFn = Callable[[str], Optional[dict]]
# date -> last event of that day ({"start":..., "end":...}) or None
LastMeetingLookupFn = Callable[[dt.date], Optional[dict]]


class UnresolvedReferenceError(Exception):
    """Raised when a named event or contextual reference can't be found. The dialogue layer
    (Phase 3) should turn this into a clarifying question, never a crash or a guessed date."""


def resolve(
    expr: TemporalExpression,
    now: dt.datetime,
    find_event: Optional[CalendarLookupFn] = None,
    find_last_meeting: Optional[LastMeetingLookupFn] = None,
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
        return _resolve_dynamic_buffer(expr, now, find_last_meeting)
    if isinstance(expr, ContextualReference):
        raise ValueError(
            "ContextualReference has no standalone window. Call resolve_contextual_duration() "
            "first, then resolve() the other constraint already established in session state."
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
    anchor = helpers.next_occurrence(now, expr.anchor_weekday, expr.anchor_time)
    deadline = anchor - dt.timedelta(minutes=expr.buffer_minutes)
    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=now, end=deadline)],
        hard_deadline=deadline,
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

    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=start, end=end)],
    )


def _resolve_calendar_arithmetic(expr: CalendarArithmetic, now: dt.datetime) -> ResolvedConstraints:
    if expr.expression != CalendarArithmeticExpr.LAST_WEEKDAY_OF_MONTH:
        raise ValueError(f"Unhandled calendar arithmetic expression: {expr.expression}")

    day = helpers.last_weekday_of_month(now)
    start, end = helpers.business_hours_window(day)
    return ResolvedConstraints(duration_minutes=expr.duration_minutes, search_windows=[TimeWindow(start=start, end=end)])


def _resolve_relative_range(expr: RelativeRangeWithExclusions, now: dt.datetime) -> ResolvedConstraints:
    if expr.range != "next_week":
        raise ValueError(f"Unhandled range: {expr.range}")

    start_date, end_date = helpers.next_week_range(now)
    preference_value = expr.time_preference.value if expr.time_preference else None
    earliest_hour, latest_hour = helpers.time_bounds_for_preference(preference_value)
    start = dt.datetime.combine(start_date, dt.time(hour=earliest_hour))
    end = dt.datetime.combine(end_date, dt.time(hour=latest_hour))
    excluded = [helpers.weekday_index(day_name) for day_name in expr.exclude_weekdays]

    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=start, end=end)],
        excluded_weekdays=excluded,
        time_preference=expr.time_preference,
    )


def _resolve_dynamic_buffer(
    expr: DynamicBuffer, now: dt.datetime, find_last_meeting: Optional[LastMeetingLookupFn]
) -> ResolvedConstraints:
    if find_last_meeting is None:
        raise ValueError("find_last_meeting lookup is required to resolve a dynamic_buffer expression")

    today = now.date()
    last_meeting = find_last_meeting(today)
    stated_hour, stated_minute = helpers.parse_hhmm(expr.after_time)
    stated_earliest = dt.datetime.combine(today, dt.time(hour=stated_hour, minute=stated_minute))

    if last_meeting is not None:
        buffer_earliest = last_meeting["end"] + dt.timedelta(minutes=expr.buffer_minutes)
        earliest_start = max(stated_earliest, buffer_earliest)
    else:
        earliest_start = stated_earliest

    end_of_evening = dt.datetime.combine(today, dt.time(hour=helpers.DEFAULT_EVENING_END_HOUR))
    return ResolvedConstraints(
        duration_minutes=expr.duration_minutes,
        search_windows=[TimeWindow(start=earliest_start, end=end_of_evening)],
        earliest_start=earliest_start,
    )
