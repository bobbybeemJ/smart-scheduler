"""Assignment scenario 1 (deadline-driven): "45 minutes sometime before my flight Friday at
6 PM" - must work backward from a deadline. Exercises the mock LLM's actual output, not a
hand-constructed schema instance, so this tests the real extraction -> resolution seam."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import DeadlineBefore  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


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
