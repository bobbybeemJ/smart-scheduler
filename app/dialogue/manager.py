"""Orchestrates a single conversation: LLM extraction (Phase 2) -> deterministic resolution
(Phase 1) -> calendar (Phase 1's client) -> reply templates. Decides whether there's enough
information to search yet, or whether to ask a clarifying question - this is the "core behavior"
loop from the assignment brief. Still text-only; voice is wired on top of this in later phases."""

from __future__ import annotations

import datetime as dt
from typing import Optional

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
from app.state import SessionState


class DialogueManager:
    def __init__(
        self,
        now_fn=dt.datetime.now,
        find_event: Optional[CalendarLookupFn] = None,
        find_last_meeting: Optional[LastMeetingLookupFn] = None,
        state: Optional[SessionState] = None,
    ):
        self.now_fn = now_fn
        self.find_event = find_event
        self.find_last_meeting = find_last_meeting
        self.state = state or SessionState()
        self.pending_contextual_reference: Optional[str] = None

    def handle_turn(self, transcript: str) -> list[str]:
        try:
            intent = extract_intent(transcript, self.state.condensed_state())
        except LLMExtractionError:
            return templates.llm_failure()

        if isinstance(intent, DurationUpdate):
            return self._handle_duration_update(intent.duration_minutes)
        if isinstance(intent, ContextualReference):
            return self._handle_contextual_reference(intent)

        # A fresh constraint - replaces whatever was established before.
        self.state.established_expression = intent
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
            constraints = resolve(
                self.state.established_expression,
                now,
                find_event=self.find_event,
                find_last_meeting=self.find_last_meeting,
            )
        except MissingDurationError:
            return templates.ask_duration()
        except UnresolvedReferenceError as exc:
            return templates.could_not_find_reference(str(exc))

        self.state.resolved_constraints = constraints
        self.state.phase = "searching"
        return templates.present_search_window(constraints)
