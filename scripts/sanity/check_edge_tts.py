"""Sanity check: synthesizes one sentence to an audio file via edge-tts."""

import asyncio
import os
import pathlib

import edge_tts
from dotenv import load_dotenv

load_dotenv()

OUTPUT_FILE = pathlib.Path(__file__).resolve().parent.parent.parent / "scratch_tts_sample.mp3"
SENTENCE = "Testing one two three, this is the smart scheduler."


async def synthesize():
    voice = os.environ.get("TTS_VOICE", "en-US-AriaNeural")
    communicate = edge_tts.Communicate(SENTENCE, voice)
    await communicate.save(str(OUTPUT_FILE))


def main():
    asyncio.run(synthesize())
    size = OUTPUT_FILE.stat().st_size
    print(f"Wrote {OUTPUT_FILE} ({size} bytes)")
    if size == 0:
        raise SystemExit("edge-tts produced an empty file - something is wrong")


if __name__ == "__main__":
    main()
