"""Latency benchmark: measures each network hop in isolation so the 800ms end-to-end target is
checked against real numbers instead of assumed ones. Prints a plain table; paste the output
into docs/latency.md for the README.

Costs one real LLM call and one real Calendar API call - not looped, run it a couple of times by
hand if you want a spread, don't put it in a CI loop.
"""

import asyncio
import os
import time

from dotenv import load_dotenv

load_dotenv()


def bench_gemini() -> float:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return float("nan")
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
    client = genai.Client(api_key=api_key)

    start = time.perf_counter()
    client.models.generate_content(model=model, contents="Reply with exactly: pong")
    return (time.perf_counter() - start) * 1000


def bench_claude() -> float:
    import anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return float("nan")
    model = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")
    client = anthropic.Anthropic(api_key=api_key)

    start = time.perf_counter()
    client.messages.create(model=model, max_tokens=16, messages=[{"role": "user", "content": "Reply with exactly: pong"}])
    return (time.perf_counter() - start) * 1000


def bench_calendar_freebusy() -> float:
    import datetime as dt

    from app.calendar_client.client import freebusy

    try:
        now = dt.datetime.now(dt.timezone.utc)
        start = time.perf_counter()
        freebusy(now, now + dt.timedelta(hours=1))
        return (time.perf_counter() - start) * 1000
    except RuntimeError:
        return float("nan")


def bench_tts_first_byte() -> float:
    import math

    import edge_tts

    async def _run():
        voice = os.environ.get("TTS_VOICE", "en-US-AriaNeural")
        communicate = edge_tts.Communicate("This is a latency benchmark sentence.", voice)
        start = time.perf_counter()
        first_byte_ms = float("nan")
        async for chunk in communicate.stream():
            if chunk["type"] == "audio" and math.isnan(first_byte_ms):
                first_byte_ms = (time.perf_counter() - start) * 1000
        return first_byte_ms

    return asyncio.run(_run())


def main():
    results = {
        "Gemini call round-trip": bench_gemini(),
        "Claude call round-trip": bench_claude(),
        "Calendar freebusy.query round-trip": bench_calendar_freebusy(),
        "edge-tts time-to-first-audio-byte": bench_tts_first_byte(),
    }

    print("\n=== Latency benchmark (real numbers, not assumed) ===")
    for name, ms in results.items():
        value = f"{ms:.0f} ms" if ms == ms else "SKIPPED (missing credentials/key)"
        print(f"  {name}: {value}")
    print("\nCopy this into docs/latency.md for the README.")


if __name__ == "__main__":
    main()
