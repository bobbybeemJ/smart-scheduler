"""Measures the full pipeline as it stands after Phase 5: real Gemini extraction -> resolve()
-> reply templates -> clause-streamed TTS. Everything except STT and the websocket transport
(Phase 6). Costs exactly 1 real Gemini call - not looped."""

import asyncio
import time

from dotenv import load_dotenv

load_dotenv()

from app import config  # noqa: E402

config.settings.use_mock_llm = False

from app.dialogue.manager import DialogueManager  # noqa: E402
from app.tts import streamer  # noqa: E402

TRANSCRIPT = "45 minutes sometime before my flight Friday at 6 PM"


async def main():
    manager = DialogueManager()

    t0 = time.perf_counter()
    clauses = manager.handle_turn(TRANSCRIPT)
    t_intent_and_resolve = time.perf_counter()

    print(f"transcript: {TRANSCRIPT!r}")
    print(f"reply clauses: {clauses}")
    print(f"\nLLM extract_intent + resolve(): {(t_intent_and_resolve - t0) * 1000:.0f} ms")

    print("\nTTS clause synthesis:")
    first_audio_ready = None
    async for index, audio, engine in streamer.synthesize_clauses(clauses):
        now = time.perf_counter()
        if first_audio_ready is None:
            first_audio_ready = now
        print(f"  clause {index}: {len(audio)} bytes via {engine}, t+{(now - t_intent_and_resolve) * 1000:.0f}ms")

    t_end = time.perf_counter()

    print(f"\n=== Summary ===")
    print(f"Intent extraction + resolve:        {(t_intent_and_resolve - t0) * 1000:.0f} ms")
    print(f"Time to FIRST audio clause ready:   {(first_audio_ready - t0) * 1000:.0f} ms")
    print(f"Time to ALL audio clauses ready:     {(t_end - t0) * 1000:.0f} ms")
    print("\n(STT and websocket transport not included - that's Phase 6.)")


if __name__ == "__main__":
    asyncio.run(main())
