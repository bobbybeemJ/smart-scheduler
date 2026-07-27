"""Two bugs found via live testing against the real deployed service (2026-07-27), both about
silently losing context that was already established rather than merging a partial correction
into it. Uses the mock LLM - zero token cost, deterministic; the fixes themselves are pure
Python and don't depend on what the LLM returns, so these test the merge/hint logic directly
against hand-constructed state rather than needing new mock LLM responses."""

import datetime as dt

from app import config

config.settings.use_mock_llm = True

from app.dialogue.manager import DialogueManager  # noqa: E402
from app.schemas import DeadlineBefore, SimpleDateTime, TimeWindow  # noqa: E402

NOW = dt.datetime(2026, 7, 27, 9, 0)  # Monday


def _always_free(start, end):
    return []


def test_bare_day_correction_preserves_the_established_deadline_time():
    """"before my flight Friday at 6pm" offered Monday slots; replying "no, I want it on Friday
    only" was being reclassified as a bare simple_datetime for "Friday", silently discarding the
    6pm deadline entirely - it only LOOKED correct in live testing because ranking happens to
    prefer morning slots anyway, which coincidentally sit before 6pm regardless of whether the
    deadline was actually honored."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.state.established_expression = DeadlineBefore(
        anchor_weekday="friday", anchor_time="18:00", buffer_minutes=0, duration_minutes=30
    )

    merged = manager._try_merge_bare_day_correction(SimpleDateTime(raw_phrase="Friday", duration_minutes=30))

    assert isinstance(merged, DeadlineBefore)
    assert merged.anchor_weekday == "friday"
    assert merged.anchor_time == "18:00"  # the deadline must survive the correction
    assert merged.duration_minutes == 30


def test_bare_day_correction_does_not_touch_a_richer_new_statement():
    """A message that states more than just a bare weekday (e.g. also a time) is a genuine new
    request, not a partial correction - must be left alone, not force-merged."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.state.established_expression = DeadlineBefore(
        anchor_weekday="friday", anchor_time="18:00", buffer_minutes=0, duration_minutes=30
    )

    fresh = SimpleDateTime(raw_phrase="Friday afternoon", duration_minutes=30)
    merged = manager._try_merge_bare_day_correction(fresh)

    assert merged is fresh  # untouched - "afternoon" makes this a real new statement


def test_reject_with_explicit_day_part_hint_reruns_search_instead_of_asking_again():
    """"none of those work, how about the afternoon instead" was being classified as a bare
    reject_all, asking a generic "what day or time would work better?" and discarding the stated
    "afternoon" preference entirely."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.state.established_expression = SimpleDateTime(raw_phrase="tomorrow morning", duration_minutes=30)
    manager.state.duration_minutes = 30
    manager.state.phase = "confirming"
    manager.state.top_candidates = [TimeWindow(start=NOW, end=NOW + dt.timedelta(minutes=30))]

    hinted = manager._try_apply_rejection_hint("none of those work, how about the afternoon instead")

    assert hinted is not None
    assert hinted.raw_phrase == "tomorrow afternoon"


def test_reject_with_relative_later_hint_shifts_the_day_part_forward():
    """"any other options, later in the day" has no literal day-part word - just a direction -
    and should shift from whatever day-part was already being searched to the next one."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.state.established_expression = SimpleDateTime(raw_phrase="Friday afternoon", duration_minutes=30)
    manager.state.duration_minutes = 30
    manager.state.phase = "confirming"
    manager.state.top_candidates = [TimeWindow(start=NOW, end=NOW + dt.timedelta(minutes=30))]

    hinted = manager._try_apply_rejection_hint("do you have any other options, something later in the day")

    assert hinted is not None
    assert hinted.raw_phrase == "Friday evening"


def test_plain_rejection_with_no_hint_still_falls_through_to_the_generic_reply():
    """A bare rejection with no stated preference at all must not be force-matched into some
    invented day-part - this is the existing, correct behavior and must not regress."""
    manager = DialogueManager(now_fn=lambda: NOW, freebusy_fn=_always_free)
    manager.state.established_expression = SimpleDateTime(raw_phrase="tomorrow morning", duration_minutes=30)
    manager.state.duration_minutes = 30

    hinted = manager._try_apply_rejection_hint("none of those work for me")

    assert hinted is None
