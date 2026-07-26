"""Covers the hardest state-management case from the assignment brief: 'actually we need a
full hour now' must re-search using the new duration while keeping the day/time context already
established, without restarting the conversation. Also covers contextual-memory recall of a
persisted 'usual meeting' duration. Uses the mock LLM - zero token cost, deterministic."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dialogue.manager import DialogueManager  # noqa: E402
from app.schemas import SimpleDateTime  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def _always_free(start, end):
    """Fixture freebusy_fn - never hits the real Calendar API, so these tests stay
    deterministic and don't depend on (or pollute) an actual calendar's real availability."""
    return []


def test_mid_conversation_duration_change_keeps_day_time_context():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)

    first_reply = manager.handle_turn("next week, not too early, not on Wednesday")
    assert "how long" in " ".join(first_reply).lower()
    assert manager.state.duration_minutes is None
    established_before = manager.state.established_expression
    assert established_before.exclude_weekdays == ["Wednesday"]

    second_reply = manager.handle_turn("actually we need a full hour now")

    assert manager.state.duration_minutes == 60
    assert manager.state.established_expression.duration_minutes == 60
    # day/time context from turn 1 must be untouched
    assert manager.state.established_expression.exclude_weekdays == ["Wednesday"]
    assert manager.state.established_expression.week_offset == 1
    assert manager.state.established_expression.time_preference == established_before.time_preference

    constraints = manager.state.resolved_constraints
    assert constraints is not None
    assert constraints.duration_minutes == 60
    assert constraints.excluded_weekdays == [2]  # Wednesday
    reply_text = " ".join(second_reply).lower()
    assert "60" in reply_text or "hour" in reply_text


def test_duration_carries_over_to_a_fresh_day_time_pivot_without_asking_again():
    """Found via real usage: a user who pivots to a new day ("Tuesday morning" after "Thursday
    morning" didn't work out) was being asked for duration all over again on every single pivot,
    even though it was already established, because the new intent is a fresh SimpleDateTime
    (duration_minutes=None on that object) rather than an explicit duration_update. Carrying over
    self.state.duration_minutes deterministically fixes this regardless of how the LLM classifies
    the pivot."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.handle_turn("Tuesday at 2pm")
    assert manager.state.duration_minutes == 30
    assert manager.state.phase == "confirming"

    reply = manager.handle_turn("thursday morning")

    assert manager.state.duration_minutes == 30
    assert manager.state.established_expression.duration_minutes == 30
    assert manager.state.phase == "confirming"  # searched immediately, did not ask for duration again
    reply_text = " ".join(reply).lower()
    assert "how long" not in reply_text


def test_duration_update_with_unresolvable_day_time_does_not_claim_to_keep_it():
    """Found via real usage: correcting the duration while the day/time was already
    unparseable produced an incoherent double message - "keeping the day/time preference you
    already gave me" immediately followed by "what day or time works for you?" This must only
    claim to be keeping the existing preference when a search actually ran on it, not when it
    turns out there was nothing usable to keep."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.state.established_expression = SimpleDateTime(duration_minutes=30, raw_phrase="garbage unparseable phrase")
    manager.state.duration_minutes = 30

    reply = manager.handle_turn("actually we need a full hour now")

    reply_text = " ".join(reply).lower()
    assert "keeping the day/time preference" not in reply_text
    assert "day" in reply_text or "time" in reply_text


def test_duration_update_with_no_established_context_asks_instead_of_crashing():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    reply = manager.handle_turn("actually we need a full hour now")
    assert manager.state.established_expression is None
    assert "schedule" in " ".join(reply).lower() or "update" in " ".join(reply).lower()


def test_contextual_reference_recalls_persisted_duration_without_asking():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.state.usual_meeting_defaults["usual sync-up"] = 30
    # establish a day/time constraint first, same as any fresh request would
    manager.handle_turn("next week, not too early, not on Wednesday")
    assert manager.state.duration_minutes is None

    reply = manager.handle_turn("our usual sync-up")

    assert manager.state.duration_minutes == 30
    assert manager.state.established_expression.duration_minutes == 30
    assert manager.pending_contextual_reference is None
    constraints = manager.state.resolved_constraints
    assert constraints is not None
    assert constraints.duration_minutes == 30


def test_contextual_reference_unknown_asks_and_then_remembers_it():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    reply = manager.handle_turn("our usual sync-up")
    assert "long" in " ".join(reply).lower()
    assert manager.pending_contextual_reference == "our usual sync-up"

    reply2 = manager.handle_turn("actually we need a full hour now")
    assert manager.state.usual_meeting_defaults["our usual sync-up"] == 60
    assert manager.pending_contextual_reference is None
    # no day/time established yet, so it should ask for that next
    assert "day" in " ".join(reply2).lower() or "time" in " ".join(reply2).lower()
