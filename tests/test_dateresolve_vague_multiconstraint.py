"""Assignment scenario 4 (vague/multi-constraint): "next week, not too early, not on Wednesday" -
a range constraint, a day exclusion, and a soft time preference combined in one utterance."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import RelativeRangeWithExclusions, TimePreference, WeekPosition  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def test_vague_multiconstraint_extracted_correctly_from_mock_llm():
    intent = extract_intent("next week, not too early, not on Wednesday")
    assert isinstance(intent, RelativeRangeWithExclusions)
    assert intent.range == "next_week"
    assert intent.exclude_weekdays == ["Wednesday"]
    assert intent.time_preference == TimePreference.NOT_TOO_EARLY
    # Duration wasn't stated in this phrase - must stay None, not a guessed default
    # (see the duration-hallucination fix from Phase 2).
    assert intent.duration_minutes is None


def test_vague_multiconstraint_resolves_to_next_calendar_week_excluding_wednesday():
    intent = extract_intent("next week, not too early, not on Wednesday")
    intent.duration_minutes = 30  # would normally arrive via a follow-up turn

    constraints = resolve(intent, NOW)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 7, 27)  # next Monday
    assert window.end.date() == dt.date(2026, 7, 31)  # next Friday
    assert constraints.excluded_weekdays == [2]  # Wednesday, Monday=0
    assert constraints.time_preference == TimePreference.NOT_TOO_EARLY


def test_not_too_early_preference_applies_to_every_day_in_the_range_not_just_the_edges():
    """Found during Phase 7: a naive implementation only respected the preference on the first
    and last day of the range, silently allowing 9am slots on days in between."""
    from app.scheduling.slot_finder import find_available_slots

    intent = extract_intent("next week, not too early, not on Wednesday")
    intent.duration_minutes = 30
    constraints = resolve(intent, NOW)

    slots = find_available_slots(constraints, freebusy_fn=lambda start, end: [], max_results=200)
    by_day = {}
    for slot in slots:
        by_day.setdefault(slot.start.date(), []).append(slot)

    assert dt.date(2026, 7, 29) not in by_day  # Wednesday fully excluded
    for day, day_slots in by_day.items():
        assert min(s.start.hour for s in day_slots) >= 10, f"{day} had a slot before the 10am floor"


def test_late_next_week_narrows_days_not_hours():
    """"sometime late next week" - week_position is a DIFFERENT dimension from time_preference
    (hour-of-day). Found via testing the assignment's own example phrase: an earlier version
    conflated "late next week" with "not too late in the day," which actually means the
    opposite thing (prefer earlier hours) - a real semantic mismatch, not just a missing case."""
    intent = extract_intent("sometime late next week")
    assert isinstance(intent, RelativeRangeWithExclusions)
    assert intent.week_position == WeekPosition.LATE_IN_RANGE
    assert intent.time_preference is None  # must NOT be conflated with hour-of-day
    intent.duration_minutes = 30

    constraints = resolve(intent, NOW)
    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 7, 30)  # Thursday, not Monday
    assert window.end.date() == dt.date(2026, 7, 31)  # Friday
