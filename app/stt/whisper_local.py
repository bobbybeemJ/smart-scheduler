"""Shared faster-whisper wrapper - used by both the Phase 0 offline test script and the live
server-side fallback (Phase 6), so model-loading logic lives in exactly one place. Lazy-loaded:
the model is never loaded into memory until actually needed, keeping Render's idle RAM footprint
low when the fallback never triggers (see the plan's RAM risk on the free 512MB instance)."""

from __future__ import annotations

import io
from typing import Optional, Union

from faster_whisper import WhisperModel

from app.config import settings

_model: Optional[WhisperModel] = None
_loaded_model_size: Optional[str] = None


class STTFallbackDisabledError(Exception):
    """Raised when transcribe_fallback() is called while ENABLE_SERVER_STT_FALLBACK=false.
    Callers (Phase 6's websocket handler) should treat this as "no fallback available" and ask
    the user to try a browser with Web Speech API support, never crash - this is the documented
    escape hatch if Render shows memory pressure from the loaded model."""


def get_model(model_size: Optional[str] = None) -> WhisperModel:
    """Singleton per model size - avoids reloading the model on every call."""
    global _model, _loaded_model_size
    size = model_size or settings.whisper_fallback_model
    if _model is None or _loaded_model_size != size:
        _model = WhisperModel(size, device="cpu", compute_type="int8")
        _loaded_model_size = size
    return _model


def transcribe(audio: Union[str, bytes, bytearray], model_size: Optional[str] = None) -> str:
    """Accepts a file path, or raw audio bytes (e.g. a webm/opus blob captured by the browser's
    MediaRecorder). faster-whisper decodes via the bundled PyAV library - no system ffmpeg
    binary required, which matters since Render's Docker image doesn't have one installed."""
    model = get_model(model_size)
    if isinstance(audio, (bytes, bytearray)):
        audio = io.BytesIO(audio)
    segments, _info = model.transcribe(audio)
    return " ".join(segment.text.strip() for segment in segments)


def transcribe_fallback(audio: Union[str, bytes, bytearray]) -> str:
    """The live server-side path (Phase 6 calls this, never `transcribe()` directly, so the
    ENABLE_SERVER_STT_FALLBACK flag is always honored)."""
    if not settings.enable_server_stt_fallback:
        raise STTFallbackDisabledError("Server-side STT fallback is disabled (ENABLE_SERVER_STT_FALLBACK=false).")
    return transcribe(audio, model_size=settings.whisper_fallback_model)
