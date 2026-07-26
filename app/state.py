"""Per-conversation state. Only a condensed version of this (never raw transcript history) is
ever sent to the LLM - see condensed_state() - which keeps token usage flat regardless of how
long the conversation runs."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas import ResolvedConstraints, TemporalExpression, TimeWindow

Phase = Literal["gathering", "searching", "confirming", "booked"]


class SessionState(BaseModel):
    duration_minutes: Optional[int] = None
    established_expression: Optional[TemporalExpression] = None
    resolved_constraints: Optional[ResolvedConstraints] = None
    usual_meeting_defaults: dict[str, int] = Field(default_factory=dict)
    phase: Phase = "gathering"
    top_candidates: list[TimeWindow] = Field(default_factory=list)
    top_candidate_was_widened: bool = False

    @property
    def top_candidate(self) -> Optional[TimeWindow]:
        """Read-only convenience accessor - the top (first) of top_candidates, or None. Kept so
        existing callers/tests that only ever cared about "the" proposed slot don't need to
        change; new code should read top_candidates directly."""
        return self.top_candidates[0] if self.top_candidates else None

    def condensed_state(self) -> dict:
        """What actually goes to the LLM each turn - small and flat regardless of how many
        turns have happened, per the plan's flat-token-usage constraint."""
        return {
            "duration_minutes": self.duration_minutes,
            "usual_meeting_defaults": self.usual_meeting_defaults,
            "established_constraint": (
                self.established_expression.model_dump(mode="json") if self.established_expression else None
            ),
            "phase": self.phase,
            "num_offered_candidates": len(self.top_candidates),
        }

    def remember_usual_meeting(self, reference_key: str, duration_minutes: int) -> None:
        self.usual_meeting_defaults[reference_key.strip().lower()] = duration_minutes
