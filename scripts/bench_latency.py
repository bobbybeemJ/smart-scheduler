"""Phase 0 latency benchmark: measures each network hop in isolation so the 800ms end-to-end
target is checked against real numbers instead of assumed ones. Prints a plain table; paste the
output into docs/phase0_latency.md for the README.

Costs one real Gemini call and one real Calendar API call - not looped, run it a couple of times
by hand if you want a spread, don't put it in a CI loop.
"""

import asyncio
import os
import sys
import time
import pathlib

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent / "sanity"))


def bench_gemini() -> float:
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        return float("nan")
    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite")
    client = genai.Client(api_key=api_key)

    start = time.perf_counter()
    client.models.generate_content(model=model, contents="Reply with exactly: pong")
    return (time.perf_counter() - start) * 1000


def bench_calendar_freebusy() -> float:
    from check_oauth_list_calendars import build_credentials_from_env
    from googleapiclient.discovery import build
    import datetime as dt

    try:
        creds = build_credentials_from_env()
    except SystemExit:
        return float("nan")

    service = build("calendar", "v3", credentials=creds)
    now = dt.datetime.utcnow()
    body = {
        "timeMin": now.isoformat() + "Z",
        "timeMax": (now + dt.timedelta(hours=1)).isoformat() + "Z",
        "items": [{"id": "primary"}],
    }

    start = time.perf_counter()
    service.freebusy().query(body=body).execute()
    return (time.perf_counter() - start) * 1000


def bench_tts_first_byte() -> float:
    import edge_tts

    async def _run():
        voice = os.environ.get("TTS_VOICE", "en-US-AriaNeural")
        communicate = edge_tts.Communicate("This is a latency benchmark sentence.", voice)
        start = time.perf_counter()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                return (time.perf_counter() - start) * 1000
        return float("nan")

    return asyncio.run(_run())


def main():
    results = {
        "Gemini call round-trip": bench_gemini(),
        "Calendar freebusy.query round-trip": bench_calendar_freebusy(),
        "edge-tts time-to-first-audio-byte": bench_tts_first_byte(),
    }

    print("\n=== Phase 0 latency benchmark (real numbers, not assumed) ===")
    for name, ms in results.items():
        value = f"{ms:.0f} ms" if ms == ms else "SKIPPED (missing credentials/key)"
        print(f"  {name}: {value}")
    print("\nCopy this into docs/phase0_latency.md for the README.")


if __name__ == "__main__":
    main()
