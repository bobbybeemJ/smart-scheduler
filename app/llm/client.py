"""The one real network call per conversational turn (architecture principle from the plan).
Mockable via USE_MOCK_LLM so the rest of the pipeline can be built/debugged at zero token cost."""

from __future__ import annotations

import re
from typing import Optional

from google import genai
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.config import settings
from app.llm import mock_responses, prompts
from app.schemas import TemporalExpression

_client: Optional[genai.Client] = None

_DURATION_NUMBER_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*-?\s*(hours?|hrs?|minutes?|mins?)\b", re.IGNORECASE)
_HALF_HOUR_RE = re.compile(r"\bhalf\s+(?:an?\s+)?hour\b", re.IGNORECASE)
_AN_HOUR_RE = re.compile(r"\ban?\s+hour\b", re.IGNORECASE)

# Mirrors resolver.py's own _MAX_RAW_PHRASE_WORDS/_MAX_RAW_PHRASE_CHARS thresholds - that check
# exists as a last-resort safety net (reject and ask again rather than guess), this one is the
# actual fix: reconstruct a clean raw_phrase deterministically so a good turn doesn't need to
# become a re-ask at all. Char length matters alongside word count: a real observed corruption
# ("Friday afternoon b20451cf-41ee-4171-8bc5-01e4ecbe567a 2026-03-31 03:00:23.490793 UTC") is
# only 6 whitespace-separated "words" (hyphen/colon-joined, not space-joined) despite being
# obviously garbage - word count alone would have missed it.
_MAX_RAW_PHRASE_WORDS = 12
_MAX_RAW_PHRASE_CHARS = 60


def _is_implausible_raw_phrase(raw_phrase: Optional[str]) -> bool:
    return raw_phrase is None or len(raw_phrase.split()) > _MAX_RAW_PHRASE_WORDS or len(raw_phrase) > _MAX_RAW_PHRASE_CHARS

_LEADING_DURATION_PREFIX_RE = re.compile(
    r"^(?:a|an)\s+\d+(?:\.\d+)?\s*-?\s*(?:hours?|hrs?|minutes?|mins?)\s*(?:long\s+)?"
    r"(?:meeting|chat|call|sync-?up|session|appointment|catch-?up)?\s*",
    re.IGNORECASE,
)

# First recognizable date/time keyword in the transcript - used to cut away whatever precedes it
# (command verbs like "Schedule"/"Book"/"Can we meet", filler, or leaked model garbage) rather
# than only stripping a specific "a/an <duration>" prefix. Real basic queries almost always open
# with a command verb ("Schedule a meeting tomorrow...", "Book a 1 hour meeting on Friday...") so
# this needs to handle arbitrary lead-ins, not just the duration-shaped one.
_DATE_TIME_ANCHOR_RE = re.compile(
    r"\b(?:next|this|coming|tomorrow|today|tonight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|october|november|december|"
    r"morning|afternoon|evening|night|noon|midnight)\b"
    r"|\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)\b"
    r"|\b\d{1,2}\s*o'clock\b",
    re.IGNORECASE,
)

# Matches a duration mention ANYWHERE in a string (leading, trailing, or mid-sentence), unlike
# _LEADING_DURATION_PREFIX_RE above which only anchors at the very start.
_DURATION_MENTION_RE = re.compile(
    r"\b(?:for\s+)?(?:an?\s+)?\d+(?:\.\d+)?\s*-?\s*(?:hours?|hrs?|minutes?|mins?)\b"
    r"(?:\s+long)?(?:\s+(?:meeting|chat|call|sync-?up|session|appointment|catch-?up))?"
    r"|\b(?:for\s+)?half\s+(?:an?\s+)?hour\b"
    r"|\b(?:for\s+)?an?\s+hour\b",
    re.IGNORECASE,
)


def _strip_duration_mention(raw_phrase: str) -> str:
    """Deterministic cleanup for a variant of the same duration-leak bug: even when the model
    gets duration_minutes right AND raw_phrase mostly right, it sometimes leaves the duration
    wording sitting in raw_phrase alongside a real date/time instead of only in duration_minutes
    - observed on "tomorrow at 3pm for 30 minutes" -> raw_phrase='tomorrow at 3pm for 30 minutes'
    (duration_minutes correctly 30, but raw_phrase not cleaned), which then fails to parse since
    dateparser chokes on the trailing "for 30 minutes". The two existing prompt examples for
    trailing duration ("next Thursday for 30 minutes", "next Wednesday afternoon for 45 minutes")
    apparently don't generalize to a phrase that also has an explicit clock time in it - rather
    than add a third prompt example to a pattern that's already proven not to generalize
    reliably, strip it here instead."""
    cleaned = _DURATION_MENTION_RE.sub(" ", raw_phrase)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _clean_raw_phrase_fallback(transcript: str) -> Optional[str]:
    """Deterministic backstop for the same free-tier model's reasoning-leak failure mode
    (resolver.py's _reject_if_implausibly_long docstring has the full history) - reconstructs a
    clean raw_phrase from the ORIGINAL transcript whenever the model's own raw_phrase is
    corrupted (leaked reasoning, hallucinated UUIDs/timestamps, etc). Primary strategy: cut the
    transcript at the first recognizable date/time keyword, discarding whatever leads up to it
    (a command verb, "a 30 minute meeting", or anything else) - covers arbitrary lead-ins, not
    just the one duration-shaped prefix _LEADING_DURATION_PREFIX_RE strips, which is kept only
    as a fallback for the rare transcript with no recognizable date/time keyword at all (should
    not normally happen for a message the model chose to classify as simple_datetime)."""
    anchor_match = _DATE_TIME_ANCHOR_RE.search(transcript)
    candidate = transcript[anchor_match.start():].strip() if anchor_match is not None else _LEADING_DURATION_PREFIX_RE.sub("", transcript).strip()
    if candidate and not _is_implausible_raw_phrase(candidate):
        return candidate
    return None


def _extract_duration_minutes_fallback(transcript: str) -> Optional[int]:
    """Deterministic backstop for a bug prompt examples alone couldn't fix: on
    gemini-flash-lite-latest (the only model with usable free-tier quota - see project memory
    on the 20-requests/day caps hit by gemini-3.5-flash/3.6-flash), duration_minutes came back
    null 9/9 times whenever the duration phrase precedes the date/event ("a 30 minute meeting
    Tuesday at 2pm"), even after adding explicit leading-duration examples to SYSTEM_INSTRUCTION
    for exactly this pattern. Per this project's own rule - a judgment the LLM keeps getting
    wrong belongs in deterministic code, not another prompt iteration - this regexes the
    original transcript directly whenever the model leaves duration_minutes null."""
    match = _DURATION_NUMBER_RE.search(transcript)
    if match is not None:
        value = float(match.group(1))
        unit = match.group(2).lower()
        minutes = value * 60 if unit.startswith(("hour", "hr")) else value
        return int(minutes)
    if _HALF_HOUR_RE.search(transcript):
        return 30
    if _AN_HOUR_RE.search(transcript):
        return 60
    return None


def _is_transient_gemini_error(exc: BaseException) -> bool:
    """Retry on rate limits (429) and server errors (5xx) - never on 4xx validation/auth
    failures, which won't succeed on retry and would just add latency for nothing."""
    code = getattr(exc, "code", None)
    return code == 429 or (isinstance(code, int) and 500 <= code < 600)


# One retry, ~1s backoff - real reliability against a single transient blip, without turning a
# failing turn into a multi-second wait (tenacity was a declared dependency from the very start
# of this project's tech stack but had never actually been wired up anywhere until now).
_retry_gemini = retry(
    retry=retry_if_exception(_is_transient_gemini_error),
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=1, max=2),
    reraise=True,
)


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


@_retry_gemini
def _call_gemini(transcript: str, condensed_state: Optional[dict]):
    client = _get_client()
    return client.models.generate_content(
        model=settings.gemini_model,
        contents=prompts.build_user_content(transcript, condensed_state),
        config={
            "system_instruction": prompts.SYSTEM_INSTRUCTION,
            "response_mime_type": "application/json",
            "response_schema": TemporalExpression,
        },
    )


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
        response = _call_gemini(transcript, condensed_state)
    except Exception as exc:  # network error, quota error, etc. (after retries are exhausted)
        raise LLMExtractionError(f"Gemini call failed: {exc}") from exc

    if response.parsed is None:
        raise LLMExtractionError(f"Gemini response did not validate against TemporalExpression: {response.text!r}")
    intent = response.parsed
    if getattr(intent, "duration_minutes", None) is None:
        fallback_minutes = _extract_duration_minutes_fallback(transcript)
        if fallback_minutes is not None:
            intent.duration_minutes = fallback_minutes
    if getattr(intent, "kind", None) == "simple_datetime":
        raw_phrase = getattr(intent, "raw_phrase", None)
        # A null raw_phrase is only worth recovering if the transcript actually names a
        # day/time the model should have used instead - a null with no such anchor at all
        # ("I need to schedule a meeting") is the legitimate "nothing stated yet" signal and
        # must NOT be replaced with the bare transcript, which would just fail dateparser later
        # for the wrong reason (nonsense phrase) instead of the right one (nothing stated).
        needs_recovery = (raw_phrase is not None and _is_implausible_raw_phrase(raw_phrase)) or (
            raw_phrase is None and _DATE_TIME_ANCHOR_RE.search(transcript) is not None
        )
        if needs_recovery:
            cleaned_phrase = _clean_raw_phrase_fallback(transcript)
            if cleaned_phrase is not None:
                raw_phrase = cleaned_phrase
                intent.raw_phrase = cleaned_phrase
        if raw_phrase is not None:
            stripped_phrase = _strip_duration_mention(raw_phrase)
            if stripped_phrase and stripped_phrase != raw_phrase:
                intent.raw_phrase = stripped_phrase
    return intent
