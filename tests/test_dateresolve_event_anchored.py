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
from app.scheduling.slot_finder import find_available_slots  # noqa: E402
from tests.fixtures.calendar_fixtures import PROJECT_ALPHA_KICKOFF, make_find_event  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def _always_free(start, end):
    return []


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


def test_event_relative_earliest_time_extracted_from_mock_llm():
    """"not before 11am" - found missing entirely via testing real Gemini: the constraint was
    silently dropped, with no error and no trace of it in the extracted intent."""
    intent = extract_intent("a 15-minute chat with the team, not before 11am, a day or two after the kickoff event")
    assert isinstance(intent, EventRelative)
    assert intent.earliest_time == "11:00"

    find_event = make_find_event({"Project Alpha Kick-off": PROJECT_ALPHA_KICKOFF})
    constraints = resolve(intent, NOW, find_event=find_event)
    assert constraints.earliest_hour == 11


def test_event_relative_earliest_time_floor_applies_across_both_offset_days():
    """The 11am floor must apply on both days of the offset_days_min..max window (Tue and Wed
    here), not just whichever one the resolver happens to treat as "first"."""
    intent = EventRelative(
        duration_minutes=15, event_name="Project Alpha Kick-off", offset_days_min=1, offset_days_max=2, earliest_time="11:00"
    )
    find_event = make_find_event({"Project Alpha Kick-off": PROJECT_ALPHA_KICKOFF})
    constraints = resolve(intent, NOW, find_event=find_event)

    slots = find_available_slots(constraints, freebusy_fn=_always_free, max_results=200)
    by_day: dict[dt.date, list] = {}
    for slot in slots:
        by_day.setdefault(slot.start.date(), []).append(slot)

    assert len(by_day) == 2, f"expected candidates on both offset days, got {sorted(by_day)}"
    for day, day_slots in by_day.items():
        earliest = min(s.start for s in day_slots)
        assert earliest.hour >= 11, f"{day}: earliest candidate {earliest} violates the 11am floor"
