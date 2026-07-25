"""Per-conversation state. Only a condensed version of this (never raw transcript history) is
ever sent to the LLM - see condensed_state() - which keeps token usage flat regardless of how
long the conversation runs."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.schemas import ResolvedConstraints, TemporalExpression

Phase = Literal["gathering", "searching", "confirming", "booked"]


class SessionState(BaseModel):
    duration_minutes: Optional[int] = None
    established_expression: Optional[TemporalExpression] = None
    resolved_constraints: Optional[ResolvedConstraints] = None
    usual_meeting_defaults: dict[str, int] = Field(default_factory=dict)
    phase: Phase = "gathering"

    def condensed_state(self) -> dict:
        """What actually goes to the LLM each turn - small and flat regardless of how many
        turns have happened, per the plan's flat-token-usage constraint."""
        return {
            "duration_minutes": self.duration_minutes,
            "usual_meeting_defaults": self.usual_meeting_defaults,
            "established_constraint": (
                self.established_expression.model_dump(mode="json") if self.established_expression else None
            ),
        }

    def remember_usual_meeting(self, reference_key: str, duration_minutes: int) -> None:
        self.usual_meeting_defaults[reference_key.strip().lower()] = duration_minutes
