"""Covers the newest DialogueManager features that only had manual/scripted verification before:
multi-slot presentation with ordinal selection, per-conversation calendar-lookup caching, and
the opt-in persistence integration. All deterministic, fixture-based, zero network."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dialogue.manager import DialogueManager  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)  # Wednesday


def _always_free(start, end):
    return []


def test_multiple_candidates_are_offered_and_ordinal_selection_books_the_right_one():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    reply = manager.handle_turn("1-hour meeting for the last weekday of this month")

    assert len(manager.state.top_candidates) > 1, "expected multiple ranked options to be offered"
    reply_text = " ".join(reply).lower()
    assert "options" in reply_text or "which one" in reply_text

    second_option = manager.state.top_candidates[1]
    reply2 = manager.handle_turn("the second one")

    assert manager.state.phase == "booked"
    reply_text2 = " ".join(reply2).lower()
    assert second_option.start.strftime("%I:%M").lstrip("0") in " ".join(reply2)


def test_bare_confirmation_still_books_the_first_option_by_default():
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.handle_turn("1-hour meeting for the last weekday of this month")
    first_option = manager.state.top_candidates[0]

    manager.handle_turn("yes")

    assert manager.state.phase == "booked"


def test_calendar_lookup_cache_avoids_a_second_real_call_for_the_same_event():
    call_count = {"n": 0}

    def counting_find_event(name):
        call_count["n"] += 1
        return {"summary": name, "start": dt.datetime(2026, 7, 20, 10, 0), "end": dt.datetime(2026, 7, 20, 11, 0)}

    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free, find_event=counting_find_event)
    manager.handle_turn("a 15-minute chat a day or two after the Project Alpha Kick-off event")
    assert call_count["n"] == 1

    # A mid-conversation duration change re-triggers resolve() for the SAME established
    # event_relative expression - this used to re-query the same event fresh every time.
    manager.handle_turn("actually we need a full hour now")
    assert call_count["n"] == 1, "expected the cached lookup to be reused, not queried again"


def test_persistence_survives_across_separate_dialogue_managers(tmp_path, monkeypatch):
    import app.persistence as persistence_module

    store_path = tmp_path / "usual_meeting_defaults.json"
    monkeypatch.setattr(persistence_module, "_DEFAULT_STORE_PATH", store_path)

    manager1 = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free, persist_usual_meeting_defaults=True)
    manager1.handle_turn("our usual sync-up")  # unknown yet - asks and remembers the reference
    manager1.handle_turn("actually we need a full hour now")  # supplies 60, gets persisted

    assert store_path.exists()

    # A brand new manager (simulating a reconnect) should recall it without asking.
    manager2 = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free, persist_usual_meeting_defaults=True)
    manager2.handle_turn("next week, not too early, not on Wednesday")
    reply = manager2.handle_turn("our usual sync-up")

    assert manager2.state.duration_minutes == 60
    reply_text = " ".join(reply).lower()
    assert "how long" not in reply_text


def test_persistence_disabled_by_default_does_not_touch_disk(tmp_path, monkeypatch):
    import app.persistence as persistence_module

    store_path = tmp_path / "should_not_be_created.json"
    monkeypatch.setattr(persistence_module, "_DEFAULT_STORE_PATH", store_path)

    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)  # persist flag left at default (off)
    manager.handle_turn("our usual sync-up")
    manager.handle_turn("actually we need a full hour now")

    assert not store_path.exists()
