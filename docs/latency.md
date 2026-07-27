# Latency benchmark (real numbers)

Two rounds of measurement, roughly a day apart, spanning a platform swap (local dev - Render was
never actually load-tested at the deployed level before being replaced, see the README's
"Explored and rejected: Render" section) and an LLM swap (Gemini -> Claude).

## Round 1: isolated hops, local dev, 2026-07-26

Measured via `scripts/bench_latency.py`, run from a residential connection in India against
Google's APIs, before the GCP Cloud Run deployment or the Claude switch existed. Two runs shown
to indicate variance.

| Hop | Run 1 | Run 2 |
|---|---|---|
| Gemini call round-trip (`gemini-flash-lite-latest`) | 696 ms | 785 ms |
| Calendar `freebusy.query` round-trip | 1212 ms | 2623 ms |
| edge-tts time-to-first-audio-byte | 892 ms | 1025 ms |

## Round 2: isolated hops, local dev, 2026-07-27 (after the Claude switch)

Same script, same machine/network, re-run after `LLM_PROVIDER` defaulted to `anthropic`. A fresh
attempt to also re-measure Gemini in this round hung indefinitely and was aborted after 100+
seconds with no response - consistent with the free-tier quota exhaustion documented elsewhere in
this project (see `app/config.py`'s `gemini_model` docstring), not a one-off fluke.

| Hop | Run 1 | Run 2 |
|---|---|---|
| Claude call round-trip (`claude-haiku-4-5-20251001`) | 1395 ms | 2039 ms |
| Gemini call round-trip (`gemini-flash-lite-latest`) | **hung, no response after 100+s** | - |
| Calendar `freebusy.query` round-trip | 1166 ms | 1121 ms |
| edge-tts time-to-first-audio-byte | 780 ms | 771 ms |

Claude's round-trip is higher than Gemini's isolated-hop number from Round 1 - expected, since
Haiku 4.5's tool-use call sends ~2400 input tokens (10 tool schema definitions) versus a single
bare text completion for the Gemini benchmark call above, which isn't an apples-to-apples
comparison of the two models' raw speed. What Round 1's number does NOT capture is reliability
under real multi-turn use - see the README's "Why Claude over Gemini" section for the accuracy
comparison that actually drove the switch; latency was a secondary factor.

## Round 3: full end-to-end, live deployed service (GCP Cloud Run, Claude), 2026-07-27

Measured via `scripts/dev/bench_deployed.py` against the real deployed URL
(`wss://smart-scheduler-693682345402.asia-south2.run.app/ws`), client-observed (includes the
real network round trip from a residential connection to the Cloud Run instance and back) - this
is the only row in this document that reflects the actual deployed stack end-to-end, not an
isolated hop.

| Phrase | Filler audio | Reply text ready | First real audio clause | All clauses ready |
|---|---|---|---|---|
| "1-hour meeting for the last weekday of this month" | 19 ms | 2397 ms | 3477 ms | 3563 ms |
| "Tuesday at 2pm for 30 minutes" | 20 ms | 1751 ms | 1756 ms | 2840 ms |
| "45 minutes sometime before my flight Friday at 6 PM" | 20 ms | 2233 ms | 3688 ms | 3688 ms |

**Takeaway**: summing even the fastest isolated hops from Round 1/2 alone already exceeds the
800ms end-to-end target from the assignment brief before STT finalization or dialogue logic are
even counted, and Round 3 confirms this directly - full end-to-end (reply text ready) ranges
~1.75-2.4s in practice, ~2.8-3.7s until all TTS audio has finished synthesizing. The 800ms target
is not realistically achievable for the full voice-in/voice-out round trip on this stack. The
perceived-latency mask (filler audio arriving in under 20ms every time, well before the real
work finishes) is the actual mitigation deployed - not a claim that raw latency hits 800ms, but a
deliberate design choice to make the *felt* latency much lower than the real number. See the
README's "Voice pipeline" section for the concurrent-synthesis and clause-streaming techniques
behind the gap between "reply text ready" and "all clauses ready" being smaller than the sum of
three sequential TTS calls would be.

Re-measure after any further deployment, region, or LLM provider change - `scripts/bench_latency.py`
for isolated hops, `scripts/dev/bench_deployed.py "<phrase>"` for a live full-turn measurement.
