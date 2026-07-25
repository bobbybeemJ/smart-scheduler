"""The one real network call per conversational turn (architecture principle from the plan).
Mockable via USE_MOCK_LLM so the rest of the pipeline can be built/debugged at zero token cost."""

from __future__ import annotations

from typing import Optional

from google import genai

from app.config import settings
from app.llm import mock_responses, prompts
from app.schemas import TemporalExpression

_client: Optional[genai.Client] = None


class LLMExtractionError(Exception):
    """Raised when the real Gemini call fails, or returns something that doesn't validate
    against TemporalExpression. The dialogue layer (Phase 3) should turn this into a
    clarifying/retry reply, never a crash - see the plan's graceful-degradation requirement."""


def _get_client() -> genai.Client:
    """Singleton, built once per process - avoids repeating the client-setup cost on every
    turn (see the plan's latency optimization backlog)."""
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def extract_intent(transcript: str, condensed_state: Optional[dict] = None) -> TemporalExpression:
    if settings.use_mock_llm:
        try:
            return mock_responses.get_mock_intent(transcript)
        except LookupError as exc:
            # An unmatched mock phrase should degrade the same way a real LLM failure would
            # (ask again), not crash the turn - found via a test that assumed this was already
            # true and hit an uncaught LookupError instead.
            raise LLMExtractionError(f"No mock response for {transcript!r}: {exc}") from exc

    try:
        client = _get_client()
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=prompts.build_user_content(transcript, condensed_state),
            config={
                "system_instruction": prompts.SYSTEM_INSTRUCTION,
                "response_mime_type": "application/json",
                "response_schema": TemporalExpression,
            },
        )
    except Exception as exc:  # network error, quota error, etc.
        raise LLMExtractionError(f"Gemini call failed: {exc}") from exc

    if response.parsed is None:
        raise LLMExtractionError(f"Gemini response did not validate against TemporalExpression: {response.text!r}")
    return response.parsed
