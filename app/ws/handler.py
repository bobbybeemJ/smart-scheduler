"""Websocket endpoint: one DialogueManager per connection (one conversation). Receives either
source of transcript (Web Speech API text, or a MediaRecorder blob for server-side fallback
transcription), runs a full turn, and streams the reply back as text immediately followed by
each clause's audio as soon as it's synthesized. Also where per-turn latency telemetry gets
assembled - manager.last_turn_timing already has llm/resolve/calendar times, this module adds
stt/tts/total and logs the complete picture."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.calendar_client import client as calendar_client
from app.dialogue.manager import DialogueManager
from app.stt.whisper_local import STTFallbackDisabledError, transcribe_fallback
from app.telemetry.timing import Stopwatch
from app.tts.streamer import ENGINE_MIME_TYPES, synthesize_clauses, synthesize_filler
from app.ws.protocol import AudioChunkMessage, AudioClauseMessage, ErrorMessage, ReplyClausesMessage, TranscriptMessage

logger = logging.getLogger(__name__)


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    manager = DialogueManager(
        find_event=calendar_client.find_event_by_name,
        find_last_meeting=calendar_client.find_last_event_of_day,
        freebusy_fn=calendar_client.freebusy,
        insert_event_fn=calendar_client.insert_event,
        persist_usual_meeting_defaults=True,
    )
    audio_buffer = bytearray()

    try:
        while True:
            raw = await websocket.receive_json()
            msg_type = raw.get("type")

            if msg_type == "transcript":
                msg = TranscriptMessage.model_validate(raw)
                logger.info("transcript (%s): %r", msg.source, msg.text)
                await _handle_turn(websocket, manager, msg.text, stt_ms=0.0)

            elif msg_type == "audio_chunk":
                msg = AudioChunkMessage.model_validate(raw)
                audio_buffer.extend(base64.b64decode(msg.data_base64))
                if msg.is_final:
                    text, stt_ms = await _transcribe_fallback_safe(websocket, bytes(audio_buffer))
                    audio_buffer.clear()
                    if text:
                        logger.info("transcript (whisper_fallback): %r", text)
                        await _handle_turn(websocket, manager, text, stt_ms=stt_ms)

            else:
                await websocket.send_json(ErrorMessage(message=f"Unknown message type: {msg_type!r}").model_dump())

    except WebSocketDisconnect:
        logger.info("Client disconnected")


async def _transcribe_fallback_safe(websocket: WebSocket, audio_bytes: bytes) -> tuple[Optional[str], float]:
    try:
        with Stopwatch() as sw:
            text = transcribe_fallback(audio_bytes)
        return text, sw.elapsed_ms
    except STTFallbackDisabledError:
        await websocket.send_json(
            ErrorMessage(message="Voice fallback isn't available right now - please try Chrome or Edge.").model_dump()
        )
    except Exception:
        logger.exception("Fallback transcription failed")
        await websocket.send_json(ErrorMessage(message="Sorry, I couldn't understand that audio.").model_dump())
    return None, 0.0


async def _handle_turn(websocket: WebSocket, manager: DialogueManager, text: str, stt_ms: float) -> None:
    turn_start = time.perf_counter()

    # Perceived-latency mask: play a short, cache-warmed filler immediately, before the real
    # (potentially multi-second) LLM/resolve/calendar work even starts, so the user hears
    # something right away instead of sitting in silence. Best-effort - a filler failure should
    # never block the real reply.
    try:
        filler_audio, filler_engine = await synthesize_filler()
        await websocket.send_json(
            AudioClauseMessage(
                index=-1,
                audio_base64=base64.b64encode(filler_audio).decode("ascii"),
                mime_type=ENGINE_MIME_TYPES.get(filler_engine, "audio/mpeg"),
            ).model_dump()
        )
    except Exception:
        logger.exception("Filler synthesis failed - continuing without it")

    try:
        # manager.handle_turn() makes blocking synchronous calls (Gemini, Calendar) - running it
        # directly here would freeze the whole async event loop (and every other connection)
        # for the entire duration of every turn. Offloading to a thread keeps the server
        # responsive and lets the filler audio above actually get sent before this finishes.
        loop = asyncio.get_running_loop()
        clauses = await loop.run_in_executor(None, manager.handle_turn, text)
    except Exception:
        # Defense-in-depth crash guard - manager.py already handles specific Calendar/LLM
        # failures gracefully; this catches anything unexpected that slips past it.
        logger.exception("Unexpected error handling turn")
        await websocket.send_json(
            ErrorMessage(message="Sorry, something went wrong on my end. Could you try again?").model_dump()
        )
        return

    timing = manager.last_turn_timing
    if timing is not None:
        timing.stt_ms = stt_ms

    await websocket.send_json(ReplyClausesMessage(clauses=clauses).model_dump())

    tts_start = time.perf_counter()
    tts_first_clause_ms: Optional[float] = None
    try:
        async for index, audio, engine in synthesize_clauses(clauses):
            if tts_first_clause_ms is None:
                tts_first_clause_ms = (time.perf_counter() - tts_start) * 1000
            message = AudioClauseMessage(
                index=index,
                audio_base64=base64.b64encode(audio).decode("ascii"),
                mime_type=ENGINE_MIME_TYPES.get(engine, "audio/mpeg"),
            )
            await websocket.send_json(message.model_dump())
    except Exception:
        logger.exception("TTS synthesis failed for all clauses")
        await websocket.send_json(
            ErrorMessage(message="I couldn't generate voice for that reply, but the text is above.").model_dump()
        )

    if timing is not None:
        timing.tts_first_clause_ms = tts_first_clause_ms
        timing.tts_total_ms = (time.perf_counter() - tts_start) * 1000
        timing.total_ms = (time.perf_counter() - turn_start) * 1000
        timing.log(text)
