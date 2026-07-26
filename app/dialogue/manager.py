"""Orchestrates a single conversation: LLM extraction (Phase 2) -> deterministic resolution
(Phase 1) -> real freebusy conflict checking + ranking (Phase 7) -> calendar write -> reply
templates. Decides whether there's enough information to search yet, or whether to ask a
clarifying question - this is the "core behavior" loop from the assignment brief."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from app import persistence
from app.calendar_client.client import freebusy as real_freebusy
from app.calendar_client.client import insert_event as real_insert_event
from app.dateresolve.resolver import (
    CalendarLookupFn,
    LastMeetingLookupFn,
    MissingAnchorTimeError,
    MissingDurationError,
    PastDateError,
    UnresolvedReferenceError,
    resolve,
    resolve_contextual_duration,
)
from app.dialogue import templates
from app.llm.client import LLMExtractionError, extract_intent
from app.schemas import ContextualReference, DurationUpdate, OutOfScope, SimpleDateTime, SlotDecision
from app.scheduling.ranking import rank_candidates
from app.scheduling.slot_finder import FreebusyFn, find_available_slots_with_fallback
from app.state import SessionState
from app.telemetry.timing import Stopwatch, TurnTiming

logger = logging.getLogger(__name__)

MAX_OFFERED_SLOTS = 3

_CONFIRMATION_PHRASES = {
    "yes",
    "yes please",
    "yeah",
    "yep",
    "sure",
    "book it",
    "sounds good",
    "that works",
    "confirm",
    "perfect",
    "great",
    "go ahead",
    "please do",
}

# Ordinal phrasing -> 0-based index, for picking among multiple offered slots
# ("the second one", "option 2"). Checked before the bare-confirmation fallback (which always
# means "the first/top option") so "yes" still works exactly as it did with a single slot.
_ORDINAL_SELECTORS = {
    "first": 0,
    "1st": 0,
    "option one": 0,
    "option 1": 0,
    "second": 1,
    "2nd": 1,
    "option two": 1,
    "option 2": 1,
    "third": 2,
    "3rd": 2,
    "option three": 2,
    "option 3": 2,
}


_FAST_PATH_MAX_WORDS = 5


def _looks_like_confirmation(text: str) -> bool:
    normalized = text.strip().lower().rstrip(".!")
    return normalized in _CONFIRMATION_PHRASES or any(phrase in normalized for phrase in _CONFIRMATION_PHRASES)


def _parse_slot_selection(text: str, num_offered: int) -> Optional[int]:
    """Returns the 0-based index the user picked, or None if the message isn't a selection/
    confirmation at all (in which case the caller should fall through to normal extraction -
    e.g. the user changed their mind about the day/time instead of picking an option).

    Longer messages are deliberately never matched here, even if they happen to contain a
    phrase like "book it" - found via testing real usage: "book it for July 28 at 11am" contains
    "book it" as a substring and was matching this fast path, silently booking whichever slot was
    already offered and discarding the different time the user just stated. Anything longer than
    a short handful of words is far more likely to carry new information than to be a bare
    confirmation, so it's left to the LLM's slot_decision classification instead, which already
    knows to treat a message with its own new day/time as a fresh request, not a confirmation."""
    normalized = text.strip().lower().rstrip(".!")
    if len(normalized.split()) > _FAST_PATH_MAX_WORDS:
        return None
    for phrase, index in _ORDINAL_SELECTORS.items():
        if phrase in normalized and index < num_offered:
            return index
    if _looks_like_confirmation(normalized):
        return 0  # bare "yes"/"book it" etc. always means the top-ranked option
    return None


class DialogueManager:
    def __init__(
        self,
        now_fn=dt.datetime.now,
        find_event: Optional[CalendarLookupFn] = None,
        find_last_meeting: Optional[LastMeetingLookupFn] = None,
        freebusy_fn: Optional[FreebusyFn] = None,
        insert_event_fn=None,
        state: Optional[SessionState] = None,
        persist_usual_meeting_defaults: bool = False,
    ):
        self.now_fn = now_fn
        self.find_event = find_event
        self.find_last_meeting = find_last_meeting
        self.freebusy_fn = freebusy_fn or real_freebusy
        self.insert_event_fn = insert_event_fn or real_insert_event
        self.state = state or SessionState()
        self.pending_contextual_reference: Optional[str] = None
        self.last_turn_timing: Optional[TurnTiming] = None

        # Per-conversation cache: a repeated lookup of the same named event or the same day's
        # last meeting (e.g. after a mid-conversation duration change re-triggers resolve())
        # doesn't need to hit the real Calendar API again - found while reviewing latency, since
        # a duration-only follow-up turn was re-querying the same event/last-meeting fresh.
        self._event_cache: dict[str, Optional[dict]] = {}
        self._last_meeting_cache: dict[dt.date, Optional[dict]] = {}

        # Opt-in (see app/persistence.py) - off by default so no existing test/script gains a
        # disk dependency it didn't have before; the real app (app/ws/handler.py) turns it on.
        self.persist_usual_meeting_defaults = persist_usual_meeting_defaults
        if persist_usual_meeting_defaults:
            persisted = persistence.load_usual_meeting_defaults()
            merged = {**persisted, **self.state.usual_meeting_defaults}
            self.state.usual_meeting_defaults = merged

    def _cached_find_event(self, name: str) -> Optional[dict]:
        if name not in self._event_cache:
            self._event_cache[name] = self.find_event(name)
        return self._event_cache[name]

    def _cached_find_last_meeting(self, day: dt.date) -> Optional[dict]:
        if day not in self._last_meeting_cache:
            self._last_meeting_cache[day] = self.find_last_meeting(day)
        return self._last_meeting_cache[day]

    def handle_turn(self, transcript: str) -> list[str]:
        self.last_turn_timing = TurnTiming()

        if self.state.phase == "confirming":
            selection = _parse_slot_selection(transcript, len(self.state.top_candidates))
            if selection is not None:
                return self._book_slot(selection)
            # Not a selection/confirmation (e.g. "actually make it next week instead") - fall
            # through to normal extraction below.

        try:
            with Stopwatch() as sw:
                intent = extract_intent(transcript, self.state.condensed_state())
            self.last_turn_timing.llm_ms = sw.elapsed_ms
        except LLMExtractionError:
            self.last_turn_timing.llm_ms = sw.elapsed_ms
            return templates.llm_failure()

        if isinstance(intent, OutOfScope):
            return templates.out_of_scope()
        if isinstance(intent, SlotDecision):
            return self._handle_slot_decision(intent)
        if isinstance(intent, DurationUpdate):
            return self._handle_duration_update(intent.duration_minutes)
        if isinstance(intent, ContextualReference):
            return self._handle_contextual_reference(intent)

        # A fresh constraint - replaces whatever was established before.
        self.state.established_expression = intent
        self.state.top_candidates = []
        if intent.duration_minutes is None:
            return templates.ask_duration()
        self.state.duration_minutes = intent.duration_minutes
        return self._search_and_reply()

    def _handle_duration_update(self, duration_minutes: int) -> list[str]:
        if self.pending_contextual_reference is not None:
            self.state.remember_usual_meeting(self.pending_contextual_reference, duration_minutes)
            if self.persist_usual_meeting_defaults:
                persistence.save_usual_meeting_defaults(self.state.usual_meeting_defaults)
            self.pending_contextual_reference = None
            self.state.duration_minutes = duration_minutes
            if self.state.established_expression is None:
                return templates.ask_day_time_preference()
            self.state.established_expression.duration_minutes = duration_minutes
            return self._search_and_reply()

        if self.state.established_expression is None:
            return templates.ask_duration_for_untethered_update()

        # Distinguish a genuine mid-conversation *correction* ("actually we need a full hour
        # now", duration was already set) from simply *answering* an earlier ask_duration()
        # prompt for the first time (duration was never set yet) - found via testing the
        # assignment's own example flow ("How long should the meeting be?" -> "1 hour."), which
        # was announcing it was "keeping the day/time preference you already gave me" when none
        # had ever been given.
        is_correction = self.state.duration_minutes is not None
        self.state.established_expression.duration_minutes = duration_minutes
        self.state.duration_minutes = duration_minutes
        if is_correction:
            return templates.duration_updated(duration_minutes) + self._search_and_reply()
        return self._search_and_reply()

    def _handle_contextual_reference(self, intent: ContextualReference) -> list[str]:
        try:
            duration = resolve_contextual_duration(intent, self.state.usual_meeting_defaults)
        except UnresolvedReferenceError:
            self.pending_contextual_reference = intent.reference
            return templates.ask_duration()

        self.state.duration_minutes = duration
        if self.state.established_expression is None:
            return templates.ask_day_time_preference()
        self.state.established_expression.duration_minutes = duration
        return self._search_and_reply()

    def _handle_slot_decision(self, intent: SlotDecision) -> list[str]:
        """LLM fallback for confirm/select/reject, reached only when handle_turn's fast local
        string-match (_parse_slot_selection) couldn't classify the message - see SlotDecision's
        docstring for the real phrasing that broke without this."""
        if self.state.phase != "confirming" or not self.state.top_candidates:
            return templates.nothing_pending_to_confirm()

        if intent.decision == "confirm_top":
            return self._book_slot(0)

        if intent.decision == "select_index":
            index = intent.selected_index
            if index is None or index >= len(self.state.top_candidates):
                return templates.nothing_pending_to_confirm()
            return self._book_slot(index)

        # reject_all - none of the offered options work; ask what would, rather than guessing.
        self.state.phase = "searching"
        self.state.top_candidates = []
        return templates.slots_rejected()

    def _search_and_reply(self) -> list[str]:
        now = self.now_fn()
        find_event = self._cached_find_event if self.find_event is not None else None
        find_last_meeting = self._cached_find_last_meeting if self.find_last_meeting is not None else None
        try:
            with Stopwatch() as sw:
                constraints = resolve(
                    self.state.established_expression,
                    now,
                    find_event=find_event,
                    find_last_meeting=find_last_meeting,
                )
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
        except MissingDurationError:
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
            return templates.ask_duration()
        except MissingAnchorTimeError:
            # Distinct from ask_duration() - this is deadline_before missing its anchor_time,
            # found via testing real Gemini on "before I leave for my trip on Friday" (no time
            # stated), which invented "18:00" from nothing rather than leaving it blank.
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
            return templates.ask_deadline_time()
        except UnresolvedReferenceError as exc:
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
            if isinstance(self.state.established_expression, SimpleDateTime):
                # A SimpleDateTime that fails to parse almost always means the user hasn't
                # actually stated a day/time yet (e.g. "I need to schedule a meeting" gives the
                # LLM nothing to anchor to, but the schema still forces it to pick some "kind") -
                # found via testing the assignment's own example conversation, which opens with
                # exactly that phrase. Ask for day/time, don't claim we "couldn't find" a
                # reference - that wording belongs to actual named-event lookups instead.
                return templates.ask_day_time_preference()
            return templates.could_not_find_reference(str(exc))
        except PastDateError:
            # Distinct from the generic calendar_failure() catch-all below - this is not a
            # Calendar API problem, it's a nonsensical request ("last week"). Found via testing
            # real Gemini on "last week"/"yesterday", both of which resolved to a genuine past
            # datetime with no error at all before this check existed.
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
            return templates.cannot_schedule_in_the_past()
        except Exception:
            # Graceful degradation: a real Calendar/network failure inside resolve()'s event
            # lookups should surface as "try again," never a stack trace or a hallucinated time.
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
            logger.exception("Unexpected failure resolving constraints")
            return templates.calendar_failure()

        self.state.resolved_constraints = constraints

        try:
            with Stopwatch() as sw:
                candidates, was_widened = find_available_slots_with_fallback(constraints, freebusy_fn=self.freebusy_fn)
            self.last_turn_timing.calendar_ms += sw.elapsed_ms
        except Exception:
            self.last_turn_timing.calendar_ms += sw.elapsed_ms
            logger.exception("Unexpected failure checking calendar availability")
            return templates.calendar_failure()

        if not candidates:
            self.state.phase = "searching"
            self.state.top_candidates = []
            return templates.no_slots_even_after_widening()

        ranked = rank_candidates(candidates, constraints)
        top_n = ranked[:MAX_OFFERED_SLOTS]
        self.state.top_candidates = top_n
        self.state.top_candidate_was_widened = was_widened
        self.state.phase = "confirming"
        return templates.present_available_slot(top_n, constraints.duration_minutes, was_widened)

    def _book_slot(self, index: int = 0) -> list[str]:
        if not self.state.top_candidates or index >= len(self.state.top_candidates):
            return templates.nothing_pending_to_confirm()

        slot = self.state.top_candidates[index]
        try:
            with Stopwatch() as sw:
                self.insert_event_fn("Meeting (scheduled by NxD Smart Scheduler)", slot.start, slot.end)
            self.last_turn_timing.calendar_ms += sw.elapsed_ms
        except Exception:
            self.last_turn_timing.calendar_ms += sw.elapsed_ms
            logger.exception("Unexpected failure booking the event")
            return templates.calendar_failure()

        self.state.phase = "booked"
        self.state.top_candidates = []
        return templates.booking_confirmed(slot)
