"""Assignment scenario 6 (dynamic buffer): "evening, after 7, but I need an hour to decompress
after my last meeting" - the buffer must be computed from a real "last meeting of the day"
lookup, not a guessed duration. Covers both directions: the stated time floor winning when the
buffer doesn't push later, and the real meeting's end time winning when it does."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import UnresolvedReferenceError, resolve  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402
from app.schemas import DynamicBuffer  # noqa: E402
from app.scheduling.ranking import rank_candidates  # noqa: E402
from app.scheduling.slot_finder import find_available_slots  # noqa: E402
from tests.fixtures.calendar_fixtures import make_event, make_find_event, make_find_last_meeting  # noqa: E402

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


def test_dynamic_buffer_feeds_correctly_into_slot_finder_and_ranking():
    """Closes a real coverage gap found when the user asked why coverage wasn't 100%: resolve()
    for dynamic_buffer was well tested, but no test ever fed that ResolvedConstraints into
    find_available_slots/rank_candidates together - the earliest_start-based filtering and
    ranking logic in both those modules was never actually exercised for this scenario."""
    intent = extract_intent("evening, after 7, but I need an hour to decompress after my last meeting")
    intent.duration_minutes = 30
    last_meeting = make_event("running late meeting", dt.datetime(2026, 7, 22, 18, 45), dt.datetime(2026, 7, 22, 18, 45))
    find_last_meeting = make_find_last_meeting({NOW.date(): last_meeting})
    constraints = resolve(intent, NOW, find_last_meeting=find_last_meeting)

    def no_busy(start, end):
        return []

    slots = find_available_slots(constraints, freebusy_fn=no_busy, max_results=50)
    assert slots, "expected at least one free slot in the evening window"
    assert all(s.start >= constraints.earliest_start for s in slots), "no slot may start before the buffer-derived floor"

    # Deliberately unordered, with an early (invalid) slot nowhere near earliest_start and a
    # slot right at earliest_start - ranking must prefer the one closest to earliest_start.
    closest = min(slots, key=lambda s: s.start)
    far_slot = max(slots, key=lambda s: s.start)
    ranked = rank_candidates([far_slot, closest], constraints)
    assert ranked[0] == closest, "ranking should prefer the slot closest to the computed earliest_start"


def test_named_event_buffer_extracted_correctly_from_mock_llm():
    """"at least an hour after my call with Sarah wraps up" - found via testing real Gemini: with
    only "last_meeting_today" available and after_time required, the model stuffed "Sarah" into
    after_time (meant to be a clock time), which then crashed trying to parse it as one. This
    kind gives a named-event reference its own honest field instead."""
    intent = extract_intent("a 20 minute call, at least an hour after my call with Sarah wraps up")
    assert isinstance(intent, DynamicBuffer)
    assert intent.after_time is None
    assert intent.buffer_source == "named_event"
    assert intent.reference_event_name == "call with Sarah"


def test_named_event_buffer_resolves_relative_to_the_real_event_end_time():
    intent = DynamicBuffer(duration_minutes=20, buffer_minutes=60, buffer_source="named_event", reference_event_name="call with Sarah")
    sarah_call = make_event("call with Sarah", dt.datetime(2026, 7, 22, 14, 0), dt.datetime(2026, 7, 22, 14, 30))
    find_event = make_find_event({"call with Sarah": sarah_call})

    constraints = resolve(intent, NOW, find_event=find_event)

    # call ends 14:30 + 60min buffer = 15:30, no stated clock-time floor to compete with it
    assert constraints.earliest_start == dt.datetime(2026, 7, 22, 15, 30)


def test_named_event_buffer_with_unknown_event_raises_not_a_crash():
    """Mirrors event_relative's unknown-event handling - a clarifying question upstream, never a
    crash or a guessed time."""
    intent = DynamicBuffer(duration_minutes=20, buffer_minutes=60, buffer_source="named_event", reference_event_name="call with Sarah")
    find_event = make_find_event({})  # nothing on the calendar

    try:
        resolve(intent, NOW, find_event=find_event)
        assert False, "expected UnresolvedReferenceError"
    except UnresolvedReferenceError as exc:
        assert "call with Sarah" in str(exc)


def test_after_time_none_with_last_meeting_today_still_resolves_without_crashing():
    """after_time became optional to fix the "Sarah stuffed into a time field" crash - this
    confirms the ORIGINAL last_meeting_today path still works correctly with no stated clock
    time at all, not just the new named_event path."""
    intent = DynamicBuffer(duration_minutes=30, buffer_minutes=45)  # after_time defaults to None
    last_meeting = make_event("1:1", dt.datetime(2026, 7, 22, 17, 0), dt.datetime(2026, 7, 22, 17, 30))
    find_last_meeting = make_find_last_meeting({NOW.date(): last_meeting})

    constraints = resolve(intent, NOW, find_last_meeting=find_last_meeting)

    assert constraints.earliest_start == dt.datetime(2026, 7, 22, 18, 15)
