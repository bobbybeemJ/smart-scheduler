"""Real conflict detection: generates candidate slots within a ResolvedConstraints window and
checks each against real Calendar freebusy data - this is what makes "find a meeting slot"
actually check availability instead of just describing the search window (Phase 3's placeholder).

One freebusy query per search window (not per candidate/day) - cheaper and matches the plan's
latency optimization backlog."""

from __future__ import annotations

import datetime as dt
from typing import Callable, Iterator, Optional

from app.calendar_client.client import freebusy as real_freebusy
from app.dateresolve import helpers
from app.schemas import ResolvedConstraints, TimeWindow

FreebusyFn = Callable[[dt.datetime, dt.datetime], list[dict]]

SLOT_GRANULARITY_MINUTES = 30


def _overlaps(a_start: dt.datetime, a_end: dt.datetime, b_start: dt.datetime, b_end: dt.datetime) -> bool:
    return a_start < b_end and b_start < a_end


def _day_bounds_hours(constraints: ResolvedConstraints) -> tuple[int, int]:
    """The (floor_hour, ceiling_hour) to use for any day that isn't explicitly bounded by the
    window's own start (first day) or end (last day) - e.g. the Tuesday/Thursday in a Mon-Fri
    "next week" search. Derived from the stated time_preference so "not too early" applies
    uniformly across every day in the range, not just the first/last."""
    preference_value = constraints.time_preference.value if constraints.time_preference else None
    return helpers.time_bounds_for_preference(preference_value)


def _iter_candidate_starts(constraints: ResolvedConstraints, window: TimeWindow, duration: dt.timedelta) -> Iterator[dt.datetime]:
    if window.start.date() == window.end.date():
        # Single-day window: these bounds are already exact and intentional (could be an
        # evening-only dynamic_buffer window, a day-part window, etc.) - no further clamping.
        cursor = window.start
        if constraints.earliest_start and constraints.earliest_start > cursor:
            cursor = constraints.earliest_start
        while cursor + duration <= window.end:
            yield cursor
            cursor += dt.timedelta(minutes=SLOT_GRANULARITY_MINUTES)
        return

    # Multi-day window: only the first day's start and the last day's end are given precisely
    # by the resolver (e.g. "now" or an exact deadline) - every other boundary (first day's end,
    # last day's start, and both bounds of any day in between) uses the same derived hours, so
    # a stated preference applies consistently across the whole range, not just at the edges.
    floor_hour, ceiling_hour = _day_bounds_hours(constraints)
    current_day = window.start.date()
    last_day = window.end.date()

    while current_day <= last_day:
        if current_day.weekday() in constraints.excluded_weekdays:
            current_day += dt.timedelta(days=1)
            continue

        is_first = current_day == window.start.date()
        is_last = current_day == last_day
        day_start = window.start if is_first else dt.datetime.combine(current_day, dt.time(hour=floor_hour))
        day_end = window.end if is_last else dt.datetime.combine(current_day, dt.time(hour=ceiling_hour))

        if constraints.earliest_start and constraints.earliest_start.date() == current_day:
            day_start = max(day_start, constraints.earliest_start)

        cursor = day_start
        while cursor + duration <= day_end:
            yield cursor
            cursor += dt.timedelta(minutes=SLOT_GRANULARITY_MINUTES)

        current_day += dt.timedelta(days=1)


def find_available_slots(
    constraints: ResolvedConstraints,
    freebusy_fn: FreebusyFn = real_freebusy,
    max_results: int = 10,
) -> list[TimeWindow]:
    """Chronological list of free candidate slots (ranking.py re-sorts these by preference -
    that's not this function's job, which is purely "is this slot actually free")."""
    duration = dt.timedelta(minutes=constraints.duration_minutes)
    results: list[TimeWindow] = []

    for window in constraints.search_windows:
        busy_periods = freebusy_fn(window.start, window.end)
        for candidate_start in _iter_candidate_starts(constraints, window, duration):
            candidate_end = candidate_start + duration
            conflict = any(_overlaps(candidate_start, candidate_end, b["start"], b["end"]) for b in busy_periods)
            if not conflict:
                results.append(TimeWindow(start=candidate_start, end=candidate_end))
                if len(results) >= max_results:
                    return results

    return results


def find_available_slots_with_fallback(
    constraints: ResolvedConstraints,
    freebusy_fn: FreebusyFn = real_freebusy,
    max_results: int = 5,
) -> tuple[list[TimeWindow], bool]:
    """Returns (candidates, was_widened). If the original window has zero free slots, widens it
    by 7 days and retries once, rather than presenting a dead end - the "proactively suggest an
    alternative day/time" requirement from the assignment brief."""
    candidates = find_available_slots(constraints, freebusy_fn, max_results)
    if candidates:
        return candidates, False

    widened = constraints.model_copy(deep=True)
    widened.search_windows = [TimeWindow(start=w.start, end=w.end + dt.timedelta(days=7)) for w in widened.search_windows]
    candidates = find_available_slots(widened, freebusy_fn, max_results)
    return candidates, bool(candidates)
