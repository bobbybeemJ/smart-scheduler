"""Assignment scenario 6 (dynamic buffer): "evening, after 7, but I need an hour to decompress
after my last meeting" - the buffer must be computed from a real "last meeting of the day"
lookup, not a guessed duration. Covers both directions: the stated time floor winning when the
buffer doesn't push later, and the real meeting's end time winning when it does."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import DynamicBuffer  # noqa: E402
from tests.fixtures.calendar_fixtures import make_event, make_find_last_meeting  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def test_dynamic_buffer_extracted_correctly_from_mock_llm():
    intent = extract_intent("evening, after 7, but I need an hour to decompress after my last meeting")
    assert isinstance(intent, DynamicBuffer)
    assert intent.after_time == "19:00"
    assert intent.buffer_minutes == 60


def test_stated_time_wins_when_the_last_meeting_ends_well_before_the_buffer_would_matter():
    intent = extract_intent("evening, after 7, but I need an hour to decompress after my last meeting")
    intent.duration_minutes = 30
    last_meeting = make_event("1:1", dt.datetime(2026, 7, 22, 17, 0), dt.datetime(2026, 7, 22, 17, 30))
    find_last_meeting = make_find_last_meeting({NOW.date(): last_meeting})

    constraints = resolve(intent, NOW, find_last_meeting=find_last_meeting)

    # last meeting ends 17:30 + 60min buffer = 18:30, which is earlier than the stated 19:00
    assert constraints.earliest_start == dt.datetime(2026, 7, 22, 19, 0)


def test_real_meeting_buffer_overrides_the_stated_floor_when_it_runs_later():
    """Found during Phase 1 verification: this is the actual differentiator - the buffer must
    come from a real calendar lookup, not just default to the stated time."""
    intent = extract_intent("evening, after 7, but I need an hour to decompress after my last meeting")
    intent.duration_minutes = 30
    last_meeting = make_event("running late meeting", dt.datetime(2026, 7, 22, 18, 45), dt.datetime(2026, 7, 22, 18, 45))
    find_last_meeting = make_find_last_meeting({NOW.date(): last_meeting})

    constraints = resolve(intent, NOW, find_last_meeting=find_last_meeting)

    # last meeting ends 18:45 + 60min buffer = 19:45, which is later than the stated 19:00 floor
    assert constraints.earliest_start == dt.datetime(2026, 7, 22, 19, 45)


def test_no_meetings_today_falls_back_to_the_stated_time():
    intent = extract_intent("evening, after 7, but I need an hour to decompress after my last meeting")
    intent.duration_minutes = 30
    find_last_meeting = make_find_last_meeting({})  # nothing on the calendar today

    constraints = resolve(intent, NOW, find_last_meeting=find_last_meeting)

    assert constraints.earliest_start == dt.datetime(2026, 7, 22, 19, 0)
