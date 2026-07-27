"""The one real network call per conversational turn (architecture principle from the plan).
Mockable via USE_MOCK_LLM so the rest of the pipeline can be built/debugged at zero token cost.

Dispatches to whichever backend settings.llm_provider names - app/llm/anthropic_backend.py
(Claude, the default - see its docstring for why) or app/llm/gemini_backend.py (Gemini, kept
as a fallback option). Both expose the same extract_intent(transcript, condensed_state) ->
TemporalExpression signature and raise the same LLMExtractionError on failure, so nothing
downstream of this dispatcher needs to know which provider is active."""

from __future__ import annotations

from typing import Optional

from app.config import settings
from app.llm import anthropic_backend, gemini_backend, mock_responses
from app.llm.errors import LLMExtractionError
from app.schemas import TemporalExpression

__all__ = ["LLMExtractionError", "extract_intent"]


def extract_intent(transcript: str, condensed_state: Optional[dict] = None) -> TemporalExpression:
    if settings.use_mock_llm:
        try:
            return mock_responses.get_mock_intent(transcript)
        except LookupError as exc:
            # An unmatched mock phrase should degrade the same way a real LLM failure would
            # (ask again), not crash the turn - found via a test that assumed this was already
            # true and hit an uncaught LookupError instead.
            raise LLMExtractionError(f"No mock response for {transcript!r}: {exc}") from exc

    backend = anthropic_backend if settings.llm_provider == "anthropic" else gemini_backend
    return backend.extract_intent(transcript, condensed_state)
