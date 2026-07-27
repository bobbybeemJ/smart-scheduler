"""Offline TTS fallback, used only when edge-tts fails. pyttsx3 picks its backend per platform:
SAPI5 on Windows (what local dev testing exercises), espeak-ng on Linux - which is exactly why
the Dockerfile installs espeak-ng explicitly, since this fallback would otherwise silently not
work in the deployed container."""

from __future__ import annotations

import pathlib
import tempfile

import pyttsx3


def synthesize_bytes(text: str) -> bytes:
    """pyttsx3 has no in-memory streaming API - it writes to a file, so a temp file is used and
    read back. A fresh engine is created per call rather than a cached singleton: pyttsx3/SAPI5
    has known reuse issues across repeated calls in the same process, and this is the fallback
    path (already a degraded scenario) - correctness wins over the small extra init cost here."""
    engine = pyttsx3.init()
    tmp_path = pathlib.Path(tempfile.gettempdir()) / f"nxd_tts_fallback_{id(engine)}.wav"
    try:
        engine.save_to_file(text, str(tmp_path))
        engine.runAndWait()
        return tmp_path.read_bytes()
    finally:
        tmp_path.unlink(missing_ok=True)
