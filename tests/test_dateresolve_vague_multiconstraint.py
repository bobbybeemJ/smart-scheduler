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
    assert intent.week_offset == 1
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


def test_this_week_range_was_accepted_by_the_schema_but_never_implemented():
    """Found by testing a brand-new phrase against the live deployed service: real Gemini
    extracted "sometime this week, not on Monday" perfectly (week_offset=0,
    exclude_weekdays=["Monday"]), but resolve() raised "Unhandled range: this_week" back when
    this field was a two-value Literal["this_week","next_week"] enum that only ever implemented
    next_week. Also checks the window starts at `now`, not a generic 9am, since "this week" can
    start partway through today - a candidate slot must never be proposed in the past."""
    intent = RelativeRangeWithExclusions(duration_minutes=30, week_offset=0, exclude_weekdays=["Monday"])
    now = dt.datetime(2026, 7, 22, 14, 0)  # Wednesday, 2pm

    constraints = resolve(intent, now)

    window = constraints.search_windows[0]
    assert window.start == now  # clamped to now, not a past 9am
    assert window.end.date() == dt.date(2026, 7, 24)  # this Friday
    assert constraints.excluded_weekdays == [0]  # Monday


def test_week_offset_two_weeks_out_was_previously_unreachable():
    """Real Gemini would abandon this schema entirely for "two weeks from now" (no way to
    express it under the old this_week/next_week enum) and fall back to SimpleDateTime instead,
    silently losing exclude_weekdays in the process. week_offset=2 covers it directly now."""
    intent = RelativeRangeWithExclusions(duration_minutes=30, week_offset=2, exclude_weekdays=["Friday"])
    constraints = resolve(intent, NOW)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 8, 3)  # Monday, two weeks from NOW's week
    assert window.end.date() == dt.date(2026, 8, 7)  # Friday
    assert constraints.excluded_weekdays == [4]  # Friday


def test_negative_week_offset_is_rejected_as_a_past_date_not_silently_resolved():
    """The real bug this whole fix was prompted by: "last week" used to resolve to a genuine
    past datetime with NO error at all. week_offset=-1 must be rejected centrally by resolve()'s
    universal past-date safety net, not silently return a bygone window."""
    from app.dateresolve.resolver import PastDateError

    intent = RelativeRangeWithExclusions(duration_minutes=30, week_offset=-1)
    try:
        resolve(intent, NOW)
        assert False, "expected PastDateError for a negative week_offset"
    except PastDateError:
        pass
