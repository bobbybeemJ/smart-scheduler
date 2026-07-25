"""Websocket message contract, both directions. Defined once here so the server (Phase 6) and
the browser client agree on shape - no ad-hoc dict shapes scattered across the codebase."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class TranscriptMessage(BaseModel):
    """Client -> server. Either the browser's Web Speech API already transcribed client-side
    (source="web_speech", zero server cost - the primary path), or the server-side
    faster-whisper fallback transcribed a MediaRecorder-captured blob
    (source="whisper_fallback" - only when the browser has no Web Speech API support)."""

    type: Literal["transcript"] = "transcript"
    text: str
    source: Literal["web_speech", "whisper_fallback"]


class AudioChunkMessage(BaseModel):
    """Client -> server, fallback path only: raw audio bytes (base64-encoded webm/opus from
    MediaRecorder) for server-side transcription."""

    type: Literal["audio_chunk"] = "audio_chunk"
    data_base64: str
    is_final: bool = False


class ReplyClausesMessage(BaseModel):
    """Server -> client: the assembled reply, one clause per list entry. Phase 5/6 stream TTS
    synthesis clause-by-clause so audio starts on the first clause rather than waiting for the
    whole reply (see the plan's "streaming reinterpreted" architecture decision)."""

    type: Literal["reply_clauses"] = "reply_clauses"
    clauses: list[str]


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    message: str
