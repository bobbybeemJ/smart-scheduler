"""Websocket endpoint: one DialogueManager per connection (one conversation). Receives either
source of transcript (Web Speech API text, or a MediaRecorder blob for server-side fallback
transcription), runs a full turn, and streams the reply back as text immediately followed by
each clause's audio as soon as it's synthesized."""

from __future__ import annotations

import base64
import logging
from typing import Optional

from fastapi import WebSocket, WebSocketDisconnect

from app.calendar_client import client as calendar_client
from app.dialogue.manager import DialogueManager
from app.stt.whisper_local import STTFallbackDisabledError, transcribe_fallback
from app.tts.streamer import ENGINE_MIME_TYPES, synthesize_clauses
from app.ws.protocol import AudioChunkMessage, AudioClauseMessage, ErrorMessage, ReplyClausesMessage, TranscriptMessage

logger = logging.getLogger(__name__)


async def websocket_endpoint(websocket: WebSocket) -> None:
    await websocket.accept()
    manager = DialogueManager(
        find_event=calendar_client.find_event_by_name,
        find_last_meeting=calendar_client.find_last_event_of_day,
        freebusy_fn=calendar_client.freebusy,
        insert_event_fn=calendar_client.insert_event,
    )
    audio_buffer = bytearray()

    try:
        while True:
            raw = await websocket.receive_json()
            msg_type = raw.get("type")

            if msg_type == "transcript":
                msg = TranscriptMessage.model_validate(raw)
                logger.info("transcript (%s): %r", msg.source, msg.text)
                await _handle_turn(websocket, manager, msg.text)

            elif msg_type == "audio_chunk":
                msg = AudioChunkMessage.model_validate(raw)
                audio_buffer.extend(base64.b64decode(msg.data_base64))
                if msg.is_final:
                    text = await _transcribe_fallback_safe(websocket, bytes(audio_buffer))
                    audio_buffer.clear()
                    if text:
                        logger.info("transcript (whisper_fallback): %r", text)
                        await _handle_turn(websocket, manager, text)

            else:
                await websocket.send_json(ErrorMessage(message=f"Unknown message type: {msg_type!r}").model_dump())

    except WebSocketDisconnect:
        logger.info("Client disconnected")


async def _transcribe_fallback_safe(websocket: WebSocket, audio_bytes: bytes) -> Optional[str]:
    try:
        return transcribe_fallback(audio_bytes)
    except STTFallbackDisabledError:
        await websocket.send_json(
            ErrorMessage(message="Voice fallback isn't available right now - please try Chrome or Edge.").model_dump()
        )
    except Exception:
        logger.exception("Fallback transcription failed")
        await websocket.send_json(ErrorMessage(message="Sorry, I couldn't understand that audio.").model_dump())
    return None


async def _handle_turn(websocket: WebSocket, manager: DialogueManager, text: str) -> None:
    try:
        clauses = manager.handle_turn(text)
    except Exception:
        # Defense-in-depth crash guard for this phase - Phase 7 replaces this with specific,
        # graceful clarifying-question replies for Calendar/LLM failures instead of this generic one.
        logger.exception("Unexpected error handling turn")
        await websocket.send_json(
            ErrorMessage(message="Sorry, something went wrong on my end. Could you try again?").model_dump()
        )
        return

    await websocket.send_json(ReplyClausesMessage(clauses=clauses).model_dump())

    try:
        async for index, audio, engine in synthesize_clauses(clauses):
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
