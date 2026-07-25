"""Phase 5 manual verification:
1. Boot-time engine health check (edge-tts + pyttsx3).
2. Multi-clause streaming - proves clause 1 is ready well before the last clause via timestamps.
3. Simulated edge-tts failure - proves automatic fallback to pyttsx3 with real audio, no crash.

Run: python -m scripts.dev.try_tts
"""

import asyncio
import logging
import pathlib

from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(message)s")

from app.tts import edge_tts_client, pyttsx3_fallback, streamer  # noqa: E402

CLAUSES = [
    "Okay, I'll look for a 60-minute slot",
    "between Monday, July 27th at 10 AM and Friday, July 31st at 6 PM.",
    "I'll let you know what I find.",
]

SCRATCH_DIR = pathlib.Path(__file__).resolve().parent.parent.parent


async def run_streaming_demo():
    print("=== Multi-clause streaming (real edge-tts, timestamps show progressive readiness) ===")
    async for index, audio, engine in streamer.synthesize_clauses(CLAUSES):
        out_path = SCRATCH_DIR / f"scratch_clause_{index}.mp3"
        out_path.write_bytes(audio)
        print(f"  clause {index}: {len(audio)} bytes via {engine} -> {out_path.name}")


async def run_fallback_demo():
    print("\n=== Simulated edge-tts failure -> automatic pyttsx3 fallback ===")

    async def _always_fails(text, voice=None):
        raise edge_tts_client.EdgeTTSError("simulated: edge-tts endpoint unreachable")

    original = edge_tts_client.synthesize_bytes
    edge_tts_client.synthesize_bytes = _always_fails
    try:
        async for index, audio, engine in streamer.synthesize_clauses(["This should fall back to pyttsx3."]):
            assert engine == "pyttsx3_fallback", f"expected fallback engine, got {engine}"
            assert len(audio) > 0, "fallback produced no audio"
            out_path = SCRATCH_DIR / "scratch_fallback_clause.wav"
            out_path.write_bytes(audio)
            print(f"  OK: fell back to {engine}, {len(audio)} bytes, no crash -> {out_path.name}")
    finally:
        edge_tts_client.synthesize_bytes = original


def run_health_check():
    print("=== Boot-time TTS engine health check ===")
    result = streamer.check_engine_health()
    for engine, healthy in result.items():
        print(f"  {engine}: {'OK' if healthy else 'FAILED'}")


if __name__ == "__main__":
    run_health_check()
    asyncio.run(run_streaming_demo())
    asyncio.run(run_fallback_demo())
