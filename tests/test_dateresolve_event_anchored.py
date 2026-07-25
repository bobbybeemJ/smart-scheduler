"""Assignment scenario 2 (event-anchored): "a 15-minute chat a day or two after the Project
Alpha Kick-off event" - must query the calendar for a named event first, then reason relative
to it. Uses a fixture calendar lookup (tests/fixtures/calendar_fixtures.py), not the real API -
the real-calendar version of this scenario is documented in tests/README.md's manual checklist."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import UnresolvedReferenceError, resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import EventRelative  # noqa: E402
from tests.fixtures.calendar_fixtures import PROJECT_ALPHA_KICKOFF, make_find_event  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def test_event_relative_extracted_correctly_from_mock_llm():
    intent = extract_intent("a 15-minute chat a day or two after the Project Alpha Kick-off event")
    assert isinstance(intent, EventRelative)
    assert intent.duration_minutes == 15
    assert intent.event_name == "Project Alpha Kick-off"
    assert intent.offset_days_min == 1
    assert intent.offset_days_max == 2


def test_event_relative_resolves_relative_to_the_real_event_end_time():
    # Project Alpha Kick-off fixture: Monday 2026-07-20, 10:00-11:00.
    intent = extract_intent("a 15-minute chat a day or two after the Project Alpha Kick-off event")
    find_event = make_find_event({"Project Alpha Kick-off": PROJECT_ALPHA_KICKOFF})

    constraints = resolve(intent, NOW, find_event=find_event)

    window = constraints.search_windows[0]
    assert window.start.date() == dt.date(2026, 7, 21)  # 1 day after Monday -> Tuesday
    assert window.end.date() == dt.date(2026, 7, 22)  # 2 days after Monday -> Wednesday
    assert constraints.duration_minutes == 15


def test_event_relative_with_unknown_event_raises_not_a_dead_end():
    """A named event that isn't on the calendar must surface as a clarifying question upstream
    (see app/dialogue/templates.could_not_find_reference), never a crash or a guessed date."""
    intent = extract_intent("a 15-minute chat a day or two after the Project Alpha Kick-off event")
    find_event = make_find_event({})  # nothing on the calendar

    try:
        resolve(intent, NOW, find_event=find_event)
        assert False, "expected UnresolvedReferenceError"
    except UnresolvedReferenceError as exc:
        assert "Project Alpha Kick-off" in str(exc)
