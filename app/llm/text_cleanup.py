"""Deterministic text cleanup shared by both LLM backends. A model that already extracted
duration_minutes correctly can still leave the same duration wording sitting in a free-text
field like SimpleDateTime.raw_phrase instead of only in duration_minutes (e.g. "tomorrow at 3pm
for 30 minutes" -> raw_phrase unchanged, breaking dateparser on the trailing "for 30 minutes").
Observed consistently on gemini-flash-lite-latest and occasionally (roughly 1 in 5-10 calls in
spot testing, 2026-07-27) on claude-haiku-4-5 too - rare enough on Claude that it isn't worth
the heavier reconstruction fallbacks gemini_backend.py also needs (corruption detection,
anchor-based rebuild from the transcript), but this specific cleanup is cheap and safe regardless
of provider: it only ever removes text that's already redundant with a duration_minutes value
the model separately reported, never invents or guesses anything."""

from __future__ import annotations

import re

_DURATION_MENTION_RE = re.compile(
    r"\b(?:for\s+)?(?:an?\s+)?\d+(?:\.\d+)?\s*-?\s*(?:hours?|hrs?|minutes?|mins?)\b"
    r"(?:\s+long)?(?:\s+(?:meeting|chat|call|sync-?up|session|appointment|catch-?up))?"
    r"|\b(?:for\s+)?half\s+(?:an?\s+)?hour\b"
    r"|\b(?:for\s+)?an?\s+hour\b",
    re.IGNORECASE,
)


def strip_duration_mention(raw_phrase: str) -> str:
    cleaned = _DURATION_MENTION_RE.sub(" ", raw_phrase)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
