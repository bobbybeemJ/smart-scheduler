"""Assignment scenario 1 (deadline-driven): "45 minutes sometime before my flight Friday at
6 PM" - must work backward from a deadline. Exercises the mock LLM's actual output, not a
hand-constructed schema instance, so this tests the real extraction -> resolution seam."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import MissingAnchorTimeError, resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import DeadlineBefore  # noqa: E402
from app.scheduling.slot_finder import find_available_slots  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def _always_free(start, end):
    return []


def test_deadline_before_extracted_correctly_from_mock_llm():
    intent = extract_intent("45 minutes sometime before my flight Friday at 6 PM")
    assert isinstance(intent, DeadlineBefore)
    assert intent.duration_minutes == 45
    assert intent.anchor_weekday == "Friday"
    assert intent.anchor_time == "18:00"


def test_deadline_before_resolves_to_a_window_ending_at_the_deadline():
    intent = extract_intent("45 minutes sometime before my flight Friday at 6 PM")
    constraints = resolve(intent, NOW)

    assert constraints.duration_minutes == 45
    window = constraints.search_windows[0]
    assert window.start == NOW
    assert window.end == dt.datetime(2026, 7, 24, 18, 0)  # next Friday, 6 PM
    assert constraints.hard_deadline == dt.datetime(2026, 7, 24, 18, 0)


def test_deadline_before_with_travel_buffer_shortens_the_window():
    """A stated buffer (e.g. travel time to the airport) should pull the deadline earlier."""
    intent = DeadlineBefore(duration_minutes=45, anchor_weekday="Friday", anchor_time="18:00", buffer_minutes=30)
    constraints = resolve(intent, NOW)

    assert constraints.search_windows[0].end == dt.datetime(2026, 7, 24, 17, 30)
    assert constraints.hard_deadline == dt.datetime(2026, 7, 24, 17, 30)


def test_deadline_before_earliest_time_extracted_from_mock_llm():
    """"nothing before 9am" - found missing entirely via testing real Gemini: the constraint was
    silently dropped, with no error and no trace of it in the extracted intent."""
    intent = extract_intent("a 20 minute call sometime before my trip, but nothing before 9am")
    assert isinstance(intent, DeadlineBefore)
    assert intent.earliest_time == "09:00"

    constraints = resolve(intent, NOW)
    assert constraints.earliest_hour == 9


def test_deadline_before_earliest_time_floor_applies_across_every_day_not_just_first():
    """The floor must apply on EVERY day the window touches (Wed/Thu/Fri here), not just
    whichever day happens to be first - a naive fix that only patched the first day would still
    silently under-floor the days in between."""
    intent = DeadlineBefore(duration_minutes=30, anchor_weekday="Friday", anchor_time="18:00", earliest_time="11:00")
    constraints = resolve(intent, NOW)  # NOW is Wednesday 9am -> window spans Wed/Thu/Fri

    slots = find_available_slots(constraints, freebusy_fn=_always_free, max_results=200)
    by_day: dict[dt.date, list] = {}
    for slot in slots:
        by_day.setdefault(slot.start.date(), []).append(slot)

    assert len(by_day) == 3, f"expected candidates spanning Wed/Thu/Fri, got {sorted(by_day)}"
    for day, day_slots in by_day.items():
        earliest = min(s.start for s in day_slots)
        assert earliest.hour >= 11, f"{day}: earliest candidate {earliest} violates the 11am floor"


def test_missing_anchor_time_extracted_correctly_from_mock_llm():
    """"before I leave for my trip on Friday" (no time stated at all) - found via testing real
    Gemini: with anchor_time required, the model invented "18:00" from nothing instead of
    leaving it null, a direct violation of the "never invent a date/time" rule."""
    intent = extract_intent("a quick 10 minute call sometime before I leave for my trip on Friday")
    assert isinstance(intent, DeadlineBefore)
    assert intent.anchor_time is None


def test_missing_anchor_time_raises_instead_of_silently_resolving():
    intent = DeadlineBefore(duration_minutes=10, anchor_weekday="Friday", anchor_time=None)
    try:
        resolve(intent, NOW)
        assert False, "expected MissingAnchorTimeError"
    except MissingAnchorTimeError:
        pass
