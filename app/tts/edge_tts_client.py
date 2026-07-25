"""Primary TTS - edge-tts, an unofficial client against Microsoft's Read Aloud backend (see the
plan's risk #2: no SLA, can break or get IP-blocked without notice - that's exactly why
pyttsx3_fallback.py exists as a real fallback, not just a comment)."""

from __future__ import annotations

from typing import AsyncIterator, Optional

import edge_tts

from app.config import settings


class EdgeTTSError(Exception):
    """Raised on any edge-tts failure (network, blocked endpoint, timeout). Callers should fall
    back to pyttsx3_fallback.py, never crash or leave the user in silence."""


async def synthesize_stream(text: str, voice: Optional[str] = None) -> AsyncIterator[bytes]:
    voice = voice or settings.tts_voice
    try:
        communicate = edge_tts.Communicate(text, voice)
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                yield chunk["data"]
    except EdgeTTSError:
        raise
    except Exception as exc:
        raise EdgeTTSError(f"edge-tts synthesis failed: {exc}") from exc


async def synthesize_bytes(text: str, voice: Optional[str] = None) -> bytes:
    """Non-streaming convenience wrapper - collects the full audio into one bytes object.
    Used per-clause by streamer.py, which is where the real "streaming" benefit comes from
    (clause N+1 doesn't wait for clause N's audio to be sent, not intra-clause byte streaming)."""
    chunks = [chunk async for chunk in synthesize_stream(text, voice)]
    if not chunks:
        raise EdgeTTSError("edge-tts returned no audio data")
    return b"".join(chunks)
