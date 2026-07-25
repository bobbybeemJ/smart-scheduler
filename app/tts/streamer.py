"""Clause-chunked TTS streaming. Since replies are template-assembled (not LLM-generated - see
the plan's architecture decision), there's no free-form generation to stream token-by-token.
Clause boundaries are the natural chunk unit instead: synthesis on clause 1 starts the instant
it's assembled, and the caller can send/play it while later clauses are still synthesizing,
rather than waiting for the whole multi-sentence reply."""

from __future__ import annotations

import logging
import time
from typing import AsyncIterator

from app.tts import edge_tts_client, pyttsx3_fallback

logger = logging.getLogger(__name__)

ENGINE_MIME_TYPES = {"edge_tts": "audio/mpeg", "pyttsx3_fallback": "audio/wav"}


async def synthesize_clauses(clauses: list[str]) -> AsyncIterator[tuple[int, bytes, str]]:
    """Yields (clause_index, audio_bytes, engine_used) as each clause finishes - the caller
    (Phase 6's websocket handler) sends/plays each chunk as soon as it's ready instead of
    waiting for every clause to finish synthesizing first."""
    start = time.perf_counter()
    for index, clause in enumerate(clauses):
        clause_start = time.perf_counter()
        try:
            audio = await edge_tts_client.synthesize_bytes(clause)
            engine = "edge_tts"
        except edge_tts_client.EdgeTTSError as exc:
            logger.warning("edge-tts failed for clause %d (%r), falling back to pyttsx3: %s", index, clause, exc)
            audio = pyttsx3_fallback.synthesize_bytes(clause)
            engine = "pyttsx3_fallback"

        elapsed_ms = (time.perf_counter() - clause_start) * 1000
        since_start_ms = (time.perf_counter() - start) * 1000
        logger.info(
            "clause %d/%d ready via %s in %.0fms (t+%.0fms since reply started)",
            index + 1,
            len(clauses),
            engine,
            elapsed_ms,
            since_start_ms,
        )
        yield index, audio, engine


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
