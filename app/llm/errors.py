"""Shared across both LLM backends - kept in its own module (not in client.py or either
backend) so neither backend needs to import from the other or from the dispatcher, avoiding a
circular import."""

from __future__ import annotations


class LLMExtractionError(Exception):
    """Raised when a real LLM call fails, or returns something that doesn't validate against
    TemporalExpression. The dialogue layer (Phase 3) should turn this into a clarifying/retry
    reply, never a crash - see the plan's graceful-degradation requirement."""
