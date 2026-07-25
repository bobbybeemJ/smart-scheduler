"""Orchestrates a single conversation: LLM extraction (Phase 2) -> deterministic resolution
(Phase 1) -> real freebusy conflict checking + ranking (Phase 7) -> calendar write -> reply
templates. Decides whether there's enough information to search yet, or whether to ask a
clarifying question - this is the "core behavior" loop from the assignment brief."""

from __future__ import annotations

import datetime as dt
import logging
from typing import Optional

from app.calendar_client.client import freebusy as real_freebusy
from app.calendar_client.client import insert_event as real_insert_event
from app.dateresolve.resolver import (
    CalendarLookupFn,
    LastMeetingLookupFn,
    MissingDurationError,
    UnresolvedReferenceError,
    resolve,
    resolve_contextual_duration,
)
from app.dialogue import templates
from app.llm.client import LLMExtractionError, extract_intent
from app.schemas import ContextualReference, DurationUpdate
from app.scheduling.ranking import rank_candidates
from app.scheduling.slot_finder import FreebusyFn, find_available_slots_with_fallback
from app.state import SessionState
from app.telemetry.timing import Stopwatch, TurnTiming

logger = logging.getLogger(__name__)

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


def _looks_like_confirmation(text: str) -> bool:
    normalized = text.strip().lower().rstrip(".!")
    return normalized in _CONFIRMATION_PHRASES or any(phrase in normalized for phrase in _CONFIRMATION_PHRASES)


class DialogueManager:
    def __init__(
        self,
        now_fn=dt.datetime.now,
        find_event: Optional[CalendarLookupFn] = None,
        find_last_meeting: Optional[LastMeetingLookupFn] = None,
        freebusy_fn: Optional[FreebusyFn] = None,
        insert_event_fn=None,
        state: Optional[SessionState] = None,
    ):
        self.now_fn = now_fn
        self.find_event = find_event
        self.find_last_meeting = find_last_meeting
        self.freebusy_fn = freebusy_fn or real_freebusy
        self.insert_event_fn = insert_event_fn or real_insert_event
        self.state = state or SessionState()
        self.pending_contextual_reference: Optional[str] = None
        self.last_turn_timing: Optional[TurnTiming] = None

    def handle_turn(self, transcript: str) -> list[str]:
        self.last_turn_timing = TurnTiming()

        if self.state.phase == "confirming" and _looks_like_confirmation(transcript):
            return self._book_slot()

        try:
            with Stopwatch() as sw:
                intent = extract_intent(transcript, self.state.condensed_state())
            self.last_turn_timing.llm_ms = sw.elapsed_ms
        except LLMExtractionError:
            self.last_turn_timing.llm_ms = sw.elapsed_ms
            return templates.llm_failure()

        if isinstance(intent, DurationUpdate):
            return self._handle_duration_update(intent.duration_minutes)
        if isinstance(intent, ContextualReference):
            return self._handle_contextual_reference(intent)

        # A fresh constraint - replaces whatever was established before.
        self.state.established_expression = intent
        self.state.top_candidate = None
        if intent.duration_minutes is None:
            return templates.ask_duration()
        self.state.duration_minutes = intent.duration_minutes
        return self._search_and_reply()

    def _handle_duration_update(self, duration_minutes: int) -> list[str]:
        if self.pending_contextual_reference is not None:
            self.state.remember_usual_meeting(self.pending_contextual_reference, duration_minutes)
            self.pending_contextual_reference = None
            self.state.duration_minutes = duration_minutes
            if self.state.established_expression is None:
                return templates.ask_day_time_preference()
            self.state.established_expression.duration_minutes = duration_minutes
            return self._search_and_reply()

        if self.state.established_expression is None:
            return templates.ask_duration_for_untethered_update()

        self.state.established_expression.duration_minutes = duration_minutes
        self.state.duration_minutes = duration_minutes
        return templates.duration_updated(duration_minutes) + self._search_and_reply()

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

    def _search_and_reply(self) -> list[str]:
        now = self.now_fn()
        try:
            with Stopwatch() as sw:
                constraints = resolve(
                    self.state.established_expression,
                    now,
                    find_event=self.find_event,
                    find_last_meeting=self.find_last_meeting,
                )
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
        except MissingDurationError:
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
            return templates.ask_duration()
        except UnresolvedReferenceError as exc:
            self.last_turn_timing.resolve_ms = sw.elapsed_ms
            return templates.could_not_find_reference(str(exc))
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
            self.state.top_candidate = None
            return templates.no_slots_even_after_widening()

        ranked = rank_candidates(candidates, constraints)
        top = ranked[0]
        self.state.top_candidate = top
        self.state.top_candidate_was_widened = was_widened
        self.state.phase = "confirming"
        return templates.present_available_slot(top, constraints.duration_minutes, was_widened)

    def _book_slot(self) -> list[str]:
        if self.state.top_candidate is None:
            return templates.nothing_pending_to_confirm()

        slot = self.state.top_candidate
        try:
            with Stopwatch() as sw:
                self.insert_event_fn("Meeting (scheduled by NxD Smart Scheduler)", slot.start, slot.end)
            self.last_turn_timing.calendar_ms += sw.elapsed_ms
        except Exception:
            self.last_turn_timing.calendar_ms += sw.elapsed_ms
            logger.exception("Unexpected failure booking the event")
            return templates.calendar_failure()

        self.state.phase = "booked"
        self.state.top_candidate = None
        return templates.booking_confirmed(slot)
