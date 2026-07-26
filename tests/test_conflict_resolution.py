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
    assert booked["summary"] == "Meeting (scheduled by NxD Smart Scheduler)"  # no title was ever stated
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


def test_past_week_offset_degrades_to_clarifying_reply_not_calendar_failure():
    """A negative week_offset ("last week") must hit the distinct PastDateError path in
    manager.py, not fall through to the generic calendar_failure() catch-all - the two wordings
    are different (see templates.cannot_schedule_in_the_past vs calendar_failure) and mean
    different things to the user."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    reply = manager.handle_turn("a 30 minute meeting last week")

    reply_text = " ".join(reply).lower()
    assert "future" in reply_text
    assert manager.state.phase != "confirming"


def test_fast_path_confirmation_does_not_swallow_a_message_with_its_own_new_time():
    """"book it for July 28 at 11am" contains "book it" as a substring, which used to match the
    fast local confirmation check (_looks_like_confirmation) and silently book whichever slot
    was already offered, discarding the different time the user actually stated - found via real
    usage where this booked the wrong (originally offered) slot instead of the requested one."""
    from app.dialogue.manager import _parse_slot_selection

    assert _parse_slot_selection("book it for a totally different time with extra words", num_offered=3) is None
    assert _parse_slot_selection("book it", num_offered=3) == 0  # short bare confirmations still fast-path

    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.handle_turn("Tuesday at 2pm")
    assert manager.state.phase == "confirming"
    offered_slot = manager.state.top_candidate

    manager.handle_turn("book it for a totally different time")

    assert manager.state.phase == "confirming"
    assert manager.state.established_expression.raw_phrase == "next Friday at 11am"
    assert manager.state.top_candidate != offered_slot


def test_slot_decision_select_index_via_llm_fallback_when_fast_path_is_ambiguous():
    """"let's go with the earlier one" isn't in manager.py's hardcoded confirmation/ordinal
    phrase tables (_CONFIRMATION_PHRASES/_ORDINAL_SELECTORS), so the fast local match returns
    None and this must fall through to real LLM classification (slot_decision) instead of
    misfiring into contextual_reference - which is exactly what happened when this phrase was
    tested directly against real Gemini before slot_decision existed."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.handle_turn("Tuesday at 2pm")
    assert manager.state.phase == "confirming"
    assert manager.state.top_candidates

    reply = manager.handle_turn("let's go with the earlier one")

    assert manager.state.phase == "booked"
    reply_text = " ".join(reply).lower()
    assert "done" in reply_text or "booked" in reply_text


def test_slot_decision_confirm_top_via_llm_fallback():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.handle_turn("Tuesday at 2pm")
    assert manager.state.phase == "confirming"

    manager.handle_turn("ok let's lock that in")

    assert manager.state.phase == "booked"


def test_slot_decision_reject_all_via_llm_fallback_asks_for_a_different_time():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.handle_turn("Tuesday at 2pm")
    assert manager.state.phase == "confirming"

    reply = manager.handle_turn("none of those work for me")

    assert manager.state.phase == "searching"
    assert manager.state.top_candidates == []
    reply_text = " ".join(reply).lower()
    assert "something else" in reply_text or "work better" in reply_text


def test_out_of_scope_message_gets_a_clarifying_reply_not_a_fake_booking_attempt():
    """Found via testing real Gemini: without an explicit escape hatch, "cancel my 3pm meeting
    tomorrow" was silently coerced into a fake simple_datetime booking request (which even went
    on to ask "how long should this meeting be?") instead of being recognized as outside this
    assistant's job."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    reply = manager.handle_turn("cancel my meeting")

    reply_text = " ".join(reply).lower()
    assert "schedule" in reply_text
    assert manager.state.phase != "confirming"


def test_missing_anchor_time_gets_a_clarifying_reply_not_a_hallucinated_deadline():
    """"before I leave for my trip on Friday" (no time stated) - found via testing real Gemini:
    without anchor_time being optional + this check, the model invented "18:00" from nothing and
    the system would have silently searched against a fabricated deadline."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    reply = manager.handle_turn("a quick 10 minute call sometime before I leave for my trip on Friday")

    reply_text = " ".join(reply).lower()
    assert "time" in reply_text
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
