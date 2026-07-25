"""Covers the hardest state-management case from the assignment brief: 'actually we need a
full hour now' must re-search using the new duration while keeping the day/time context already
established, without restarting the conversation. Also covers contextual-memory recall of a
persisted 'usual meeting' duration. Uses the mock LLM - zero token cost, deterministic."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dialogue.manager import DialogueManager  # noqa: E402

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
    assert manager.state.established_expression.range == "next_week"
    assert manager.state.established_expression.time_preference == established_before.time_preference

    constraints = manager.state.resolved_constraints
    assert constraints is not None
    assert constraints.duration_minutes == 60
    assert constraints.excluded_weekdays == [2]  # Wednesday
    reply_text = " ".join(second_reply).lower()
    assert "60" in reply_text or "hour" in reply_text


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
