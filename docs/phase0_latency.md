# Phase 0 latency benchmark (real numbers)

Measured in isolation via `scripts/bench_latency.py`, run from a residential connection in India
against Google's APIs, 2026-07-26. Two runs shown to indicate variance.

| Hop | Run 1 | Run 2 |
|---|---|---|
| Gemini call round-trip (`gemini-flash-lite-latest`) | 696 ms | 785 ms |
| Calendar `freebusy.query` round-trip | 1212 ms | 2623 ms |
| edge-tts time-to-first-audio-byte | 892 ms | 1025 ms |

**Takeaway**: summing the three hops alone already exceeds the 800ms end-to-end target before
STT finalization, dialogue logic, Render's shared CPU, or Render cold starts are even counted.
The 800ms target from the assignment brief is not realistically achievable for the full
voice-in/voice-out round trip on this stack — confirmed with real measurements rather than
assumed. The perceived-latency mask (an immediate "Let me check that..." filler reply played
via TTS while the calendar lookup runs) is the mitigation, not a claim that raw latency hits
800ms. This will be re-measured once deployed to Render (Phase 10) since cold network paths and
shared CPU will likely add further latency on top of these local-network numbers.
