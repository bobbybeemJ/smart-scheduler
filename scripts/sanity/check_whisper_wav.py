"""Sanity check: exercises both edge-tts and faster-whisper in one pass - synthesize a known
sentence, then transcribe it back and print the result for a manual eyeball match. Uses the same
synthesis step as check_edge_tts.py so it doesn't require a pre-existing WAV file."""

import asyncio
import os
import pathlib

import edge_tts
from dotenv import load_dotenv
from faster_whisper import WhisperModel

load_dotenv()

AUDIO_FILE = pathlib.Path(__file__).resolve().parent.parent.parent / "scratch_tts_sample.mp3"
SENTENCE = "Testing one two three, this is the smart scheduler."


async def synthesize():
    voice = os.environ.get("TTS_VOICE", "en-US-AriaNeural")
    communicate = edge_tts.Communicate(SENTENCE, voice)
    await communicate.save(str(AUDIO_FILE))


def main():
    if not AUDIO_FILE.exists():
        asyncio.run(synthesize())

    model_size = os.environ.get("WHISPER_LOCAL_TEST_MODEL", "base.en")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")

    segments, info = model.transcribe(str(AUDIO_FILE))
    transcript = " ".join(segment.text.strip() for segment in segments)

    print(f"model: {model_size}")
    print(f"expected: {SENTENCE!r}")
    print(f"got:      {transcript!r}")


if __name__ == "__main__":
    main()
