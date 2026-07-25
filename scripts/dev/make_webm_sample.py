"""Transcodes the Phase 0 scratch_tts_sample.mp3 into a webm/opus file via PyAV, to simulate
what the browser's MediaRecorder actually produces (webm container, opus codec) - closer to
reality than testing against a clean WAV, per the plan's risk #6 (browser audio format vs. what
faster-whisper can decode)."""

import pathlib

import av

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
INPUT_MP3 = ROOT / "scratch_tts_sample.mp3"
OUTPUT_WEBM = ROOT / "scratch_tts_sample.webm"


def main():
    if not INPUT_MP3.exists():
        raise SystemExit(f"{INPUT_MP3} not found - run scripts/sanity/check_edge_tts.py first")

    input_container = av.open(str(INPUT_MP3))
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
    print(f"Wrote {OUTPUT_WEBM} ({OUTPUT_WEBM.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
