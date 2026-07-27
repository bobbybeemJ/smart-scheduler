"""Locks in a real bug found by testing the assignment brief's own primary example conversation
verbatim against real Gemini:

    User: "I need to schedule a meeting."
    Bot:  "Okay! How long should the meeting be?"
    User: "1 hour."
    Bot:  "Got it. ... Do you have a preferred day or time?"
    User: "Sometime on Tuesday afternoon."
    Bot:  "I have 2:00 PM or 4:30 PM available ..."

"I need to schedule a meeting" gives the LLM no day/time signal at all, but the schema still
forces it to pick some "kind" - real Gemini mapped it to SimpleDateTime(raw_phrase='meeting').
Resolving that garbage phrase correctly fails to parse, but the failure was being reported as
"I couldn't find that - Could not parse date/time phrase: 'meeting'..." (wording meant for a
missing named-event reference), instead of simply asking for day/time like the assignment's own
example expects. Also locks in a related wording bug: the first-ever duration answer was
incorrectly phrased as "keeping the day/time preference you already gave me" when none had been
given yet - that phrasing should only appear for a genuine later correction."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dateresolve.resolver import UnresolvedReferenceError, resolve  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402
from app.schemas import SimpleDateTime  # noqa: E402
from app.telemetry.timing import TurnTiming  # noqa: E402

NOW = dt.datetime(2026, 7, 22, 9, 0)


def _always_free(start, end):
    return []


def test_unparseable_simple_datetime_raises_unresolved_reference_error():
    """Sanity check on the resolver itself: garbage text should fail to parse, not hallucinate
    a date - this is the failure the dialogue layer needs to handle gracefully."""
    try:
        resolve(SimpleDateTime(duration_minutes=30, raw_phrase="meeting"), NOW)
        assert False, "expected UnresolvedReferenceError for unparseable text"
    except UnresolvedReferenceError:
        pass


def test_bare_scheduling_request_asks_for_day_time_not_a_confusing_error():
    """Simulates the moment right after "I need to schedule a meeting." -> "1 hour." - real
    Gemini extracted SimpleDateTime(raw_phrase='meeting') for the first message (the schema
    still forces it to pick some "kind" even with zero date/time signal to work with), and once
    a duration is attached, resolving that garbage phrase used to surface as "I couldn't find
    that - Could not parse date/time phrase: 'meeting'" instead of just asking for day/time."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.last_turn_timing = TurnTiming()  # normally set by handle_turn(); called directly here
    manager.state.established_expression = SimpleDateTime(duration_minutes=60, raw_phrase="meeting")
    manager.state.duration_minutes = 60

    reply, _ = manager._search_and_reply()

    reply_text = " ".join(reply).lower()
    assert "day" in reply_text or "time" in reply_text
    assert "couldn't find" not in reply_text


def test_first_duration_answer_does_not_claim_to_be_keeping_a_prior_preference():
    """"1 hour." answering the very first ask_duration() prompt must not be phrased as a
    correction ("keeping the day/time preference you already gave me") when no day/time was
    ever given - that wording is only correct for a genuine later change."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.last_turn_timing = TurnTiming()  # normally set by handle_turn(); called directly here
    manager.state.established_expression = SimpleDateTime(duration_minutes=None, raw_phrase="meeting")
    assert manager.state.duration_minutes is None  # never set yet - this is the first answer

    reply = manager._handle_duration_update(60)

    reply_text = " ".join(reply).lower()
    assert "keeping the day/time preference" not in reply_text


def test_genuine_duration_correction_still_uses_the_updated_wording():
    """The opposite case must still work: once a duration IS already set, a later change should
    still say it's keeping the existing day/time context."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.last_turn_timing = TurnTiming()  # normally set by handle_turn(); called directly here
    manager.state.established_expression = SimpleDateTime(duration_minutes=30, raw_phrase="Tuesday at 2pm")
    manager.state.duration_minutes = 30  # already set - this is a correction, not a first answer

    reply = manager._handle_duration_update(60)

    reply_text = " ".join(reply).lower()
    assert "keeping the day/time preference" in reply_text
