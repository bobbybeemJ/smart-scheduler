"""Phase 7 behaviors: no-slots-available proactively suggests an alternative rather than a dead
end, booking confirmation actually writes to the calendar, and a Calendar API failure degrades
to a clarifying reply instead of a crash. Uses the mock LLM and fixture freebusy/insert
functions - zero token cost, no real network calls, deterministic."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dialogue.manager import DialogueManager  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def _always_free(start, end):
    return []


def _fully_busy_until_august(start, end):
    """Busy for all of July, free from August onward. Must return a busy block that actually
    overlaps only the pre-August portion of the queried range - returning one block covering
    the whole query (keyed off just the query's end date) breaks once a widened query spans
    both the busy and free periods in a single freebusy call, as slot_finder does."""
    cutoff = dt.datetime(2026, 8, 1)
    if start < cutoff:
        return [{"start": start, "end": min(end, cutoff)}]
    return []


def _always_busy(start, end):
    return [{"start": start, "end": end}]


def test_no_slots_available_even_after_widening_gives_alternative_not_dead_end():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_busy)
    reply = manager.handle_turn("1-hour meeting for the last weekday of this month")

    reply_text = " ".join(reply).lower()
    assert "different" in reply_text or "another" in reply_text or "try" in reply_text
    assert manager.state.phase == "searching"
    assert manager.state.top_candidate is None


def test_widened_search_proactively_suggests_alternative_day():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_fully_busy_until_august)
    manager.handle_turn("next week, not too early, not on Wednesday")  # duration not stated -> asks
    reply = manager.handle_turn("actually we need a full hour now")  # supplies duration -> searches

    reply_text = " ".join(reply).lower()
    assert "couldn't find anything in your original window" in reply_text
    assert manager.state.phase == "confirming"
    assert manager.state.top_candidate is not None
    assert manager.state.top_candidate.start >= dt.datetime(2026, 8, 1)


def test_booking_confirmation_calls_insert_event_and_updates_phase():
    booked = {}

    def fake_insert_event(summary, start, end):
        booked["summary"] = summary
        booked["start"] = start
        booked["end"] = end
        return {"id": "fake-event-id"}

    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free, insert_event_fn=fake_insert_event)
    manager.handle_turn("Tuesday at 2pm")
    assert manager.state.phase == "confirming"
    offered_slot = manager.state.top_candidate

    reply = manager.handle_turn("yes, book it")

    assert booked["start"] == offered_slot.start
    assert booked["end"] == offered_slot.end
    assert manager.state.phase == "booked"
    assert manager.state.top_candidate is None
    reply_text = " ".join(reply).lower()
    assert "done" in reply_text or "booked" in reply_text


def test_confirmation_with_nothing_pending_does_not_crash():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    # "yes" with no slot ever offered - the confirmation check only fires when phase is
    # "confirming", so this goes through normal extraction. The mock has no entry for "yes",
    # which used to raise an uncaught LookupError (real bug, fixed in app/llm/client.py) -
    # now it's caught and degrades to llm_failure() instead of crashing.
    reply = manager.handle_turn("yes")
    assert reply  # got some reply, not an unhandled exception


def test_calendar_failure_during_search_degrades_gracefully():
    def _raises(start, end):
        raise RuntimeError("simulated Calendar API outage")

    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_raises)
    reply = manager.handle_turn("Tuesday at 2pm")

    reply_text = " ".join(reply).lower()
    assert "trouble" in reply_text or "try again" in reply_text
    assert manager.state.phase != "confirming"


def test_calendar_failure_during_booking_degrades_gracefully():
    def _raises(summary, start, end):
        raise RuntimeError("simulated Calendar API outage during insert")

    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free, insert_event_fn=_raises)
    manager.handle_turn("Tuesday at 2pm")
    assert manager.state.phase == "confirming"

    reply = manager.handle_turn("yes")
    reply_text = " ".join(reply).lower()
    assert "trouble" in reply_text or "try again" in reply_text
