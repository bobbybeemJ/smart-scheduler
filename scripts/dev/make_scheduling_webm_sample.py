"""Like make_webm_sample.py, but synthesizes an actual scheduling phrase (matching a mock
keyword) rather than a generic test sentence, so the audio_chunk fallback path can be
demonstrated end-to-end including a real scheduling reply, not just the graceful-error path."""

import asyncio
import pathlib

import av
import edge_tts

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
OUTPUT_MP3 = ROOT / "scratch_scheduling_sample.mp3"
OUTPUT_WEBM = ROOT / "scratch_scheduling_sample.webm"
PHRASE = "Let's meet Tuesday at 2pm"


async def synthesize():
    communicate = edge_tts.Communicate(PHRASE, "en-US-AriaNeural")
    await communicate.save(str(OUTPUT_MP3))


def transcode_to_webm():
    input_container = av.open(str(OUTPUT_MP3))
    output_container = av.open(str(OUTPUT_WEBM), mode="w", format="webm")

    in_stream = input_container.streams.audio[0]
    out_stream = output_container.add_stream("libopus", rate=48000)
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48000)

    for frame in input_container.decode(in_stream):
        for resampled in resampler.resample(frame):
            for packet in out_stream.encode(resampled):
                output_container.mux(packet)
    for packet in out_stream.encode(None):
        output_container.mux(packet)

    output_container.close()
    input_container.close()


def main():
    asyncio.run(synthesize())
    transcode_to_webm()
    print(f"Wrote {OUTPUT_WEBM} ({OUTPUT_WEBM.stat().st_size} bytes), phrase: {PHRASE!r}")


if __name__ == "__main__":
    main()
