"""Real bug found via live end-to-end testing: "Find me 30 minutes after my last meeting today"
came back with duration_minutes=None and buffer_minutes=30 - the model had to put the sentence's
only number SOMEWHERE, and schedule_dynamic_buffer's buffer_minutes was marked required in the
Claude tool schema, forcing it to invent a value there whenever no genuine buffer was stated. Same
"required field forces invention" trap this file's sibling backend already hit and fixed for
DeadlineBefore.buffer_minutes and DynamicBuffer.reference_event_name - just not applied to this
one field the first time.

This can only be caught against the real model - a mocked extract_intent (used by most of this
suite) returns a canned response per phrase and never exercises the tool schema at all. A bare
module-level `config.settings.use_mock_llm = False` is NOT enough to guarantee that, though - many
other test files set it to True at module level too, and pytest collects (runs the top-level code
of) every test module before running any test function, so whichever file happens to be collected
LAST leaves its value in effect for the whole session, regardless of which test is currently
executing. Found the hard way: this file's tests passed in isolation but silently ran in mock mode
(and failed) as part of the full suite. monkeypatch, applied inside the test function itself,
sets the value at RUN time instead and restores it afterward - the only way to make this
reliable regardless of collection order."""

from app.llm.client import extract_intent
from app.schemas import DynamicBuffer


def test_duration_before_event_anchor_goes_to_duration_not_buffer(monkeypatch):
    from app import config

    monkeypatch.setattr(config.settings, "use_mock_llm", False)
    intent = extract_intent("Find me 30 minutes after my last meeting today")
    assert isinstance(intent, DynamicBuffer)
    assert intent.duration_minutes == 30
    assert intent.buffer_minutes == 0


def test_genuine_buffer_is_still_extracted_correctly_after_the_fix(monkeypatch):
    """The fix must not regress the legitimate case - a real stated gap still belongs in
    buffer_minutes, not silently dropped."""
    from app import config

    monkeypatch.setattr(config.settings, "use_mock_llm", False)
    intent = extract_intent("I need an hour to decompress after my last meeting, then a 30 minute meeting")
    assert isinstance(intent, DynamicBuffer)
    assert intent.buffer_minutes == 60
    assert intent.duration_minutes == 30
