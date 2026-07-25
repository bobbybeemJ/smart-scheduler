"""Clause-chunked TTS streaming. Since replies are template-assembled (not LLM-generated - see
the plan's architecture decision), there's no free-form generation to stream token-by-token.
Clause boundaries are the natural chunk unit instead.

Two latency optimizations added after reviewing Phase 8's telemetry, both safe because they only
change *how fast* clauses become available, never *what* audio is produced or the order it's
delivered in:

1. In-memory clause cache - many clauses are exact boilerplate repeated across many turns
   ("Should I book it?", "Anything else?"), so synthesizing them again via a real edge-tts
   network call every single time was pure waste.
2. Parallel synthesis - clauses are independent of each other, so all of a reply's clauses now
   start synthesizing concurrently instead of one after another. Delivery is still strictly in
   order (clause 0 before clause 1 before clause 2) - only the synthesis *work* overlaps, so a
   3-clause reply's total synthesis time collapses from roughly the sum of all three clauses'
   times toward the slowest single one, instead of paying for all three sequentially."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import AsyncIterator

from app.tts import edge_tts_client, pyttsx3_fallback

logger = logging.getLogger(__name__)

ENGINE_MIME_TYPES = {"edge_tts": "audio/mpeg", "pyttsx3_fallback": "audio/wav"}

FILLER_PHRASE = "One moment..."

_clause_cache: dict[str, tuple[bytes, str]] = {}
_MAX_CACHE_ENTRIES = 200


async def _synthesize_one(clause: str) -> tuple[bytes, str]:
    cached = _clause_cache.get(clause)
    if cached is not None:
        return cached

    try:
        audio = await edge_tts_client.synthesize_bytes(clause)
        engine = "edge_tts"
    except edge_tts_client.EdgeTTSError as exc:
        logger.warning("edge-tts failed for clause %r, falling back to pyttsx3: %s", clause, exc)
        audio = pyttsx3_fallback.synthesize_bytes(clause)
        engine = "pyttsx3_fallback"

    if len(_clause_cache) < _MAX_CACHE_ENTRIES:
        _clause_cache[clause] = (audio, engine)
    return audio, engine


async def _synthesize_timed(index: int, clause: str, batch_start: float):
    clause_start = time.perf_counter()
    audio, engine = await _synthesize_one(clause)
    elapsed_ms = (time.perf_counter() - clause_start) * 1000
    since_start_ms = (time.perf_counter() - batch_start) * 1000
    return index, audio, engine, elapsed_ms, since_start_ms


async def synthesize_clauses(clauses: list[str]) -> AsyncIterator[tuple[int, bytes, str]]:
    """Yields (clause_index, audio_bytes, engine_used) in order as each clause becomes
    available - the caller (Phase 6's websocket handler) sends/plays each chunk as soon as it's
    ready. All clauses start synthesizing concurrently (see module docstring); only delivery
    order is preserved, not synthesis order."""
    batch_start = time.perf_counter()
    tasks = [asyncio.create_task(_synthesize_timed(i, clause, batch_start)) for i, clause in enumerate(clauses)]

    for task in tasks:
        index, audio, engine, elapsed_ms, since_start_ms = await task
        logger.info(
            "clause %d/%d ready via %s in %.0fms (t+%.0fms since reply started)",
            index + 1,
            len(clauses),
            engine,
            elapsed_ms,
            since_start_ms,
        )
        yield index, audio, engine


async def synthesize_filler() -> tuple[bytes, str]:
    """The perceived-latency mask: a short, cache-warmed phrase sent immediately while the real
    LLM/resolve/calendar work (which can take several real seconds) runs in the background -
    see app/ws/handler.py. Pre-warmed once at server startup (app/main.py's lifespan) so even
    the very first real turn doesn't pay a fresh synthesis cost for it."""
    return await _synthesize_one(FILLER_PHRASE)


async def check_engine_health() -> dict[str, bool]:
    """Boot-time check (Phase 6's app startup calls this once and logs the result) - gives
    visibility into risk #2, since edge-tts is unofficial and can go down without notice.
    Async because it's called from FastAPI's lifespan, which is already inside a running event
    loop - asyncio.run() cannot be called from within one (this broke at real server startup:
    "asyncio.run() cannot be called from a running event loop" - fixed by awaiting directly)."""
    result = {"edge_tts": False, "pyttsx3": False}

    try:
        await edge_tts_client.synthesize_bytes("test")
        result["edge_tts"] = True
    except Exception as exc:  # noqa: BLE001 - health check, any failure just means "not healthy"
        logger.warning("edge-tts health check failed: %s", exc)

    try:
        pyttsx3_fallback.synthesize_bytes("test")
        result["pyttsx3"] = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("pyttsx3 health check failed: %s", exc)

    return result
