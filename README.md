# NxD Smart Scheduler

A voice-enabled scheduling assistant backed by a real Google Calendar. It holds a multi-turn
conversation, resolves natural-language date/time expressions - including several genuinely hard
cases (deadline-driven, event-anchored, calendar arithmetic, vague multi-constraint ranges,
contextual "our usual sync-up" memory, and dynamic buffers relative to another meeting) - checks
real calendar availability, ranks candidate slots, and books the one the user picks.

**Live demo**: https://smart-scheduler-693682345402.asia-south2.run.app

---

## Running it

### Prerequisites

- Python 3.12
- A Google Cloud project with the Calendar API enabled, and an OAuth consent screen in Testing
  status with your Google account added as a test user
- An Anthropic API key (or a Gemini API key, if you want to run the alternate backend)

### Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env`:

- `ANTHROPIC_API_KEY` - get one from the Anthropic Console. Set `USE_MOCK_LLM=false` to actually
  call it; leave it `true` to run entirely offline against canned mock responses (useful for
  developing anything downstream of intent extraction without spending tokens).
- Google OAuth: create an OAuth client (type "Desktop app") in your GCP project, download it as
  `client_secret.json` into the repo root (gitignored - never commit it), then run:
  ```powershell
  python -m scripts.oauth_bootstrap
  ```
  This runs a one-time local consent flow and prints `GOOGLE_OAUTH_CLIENT_ID`,
  `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REFRESH_TOKEN` - copy all three into `.env`.

### Run it

Full pipeline, with voice, in a browser:

```powershell
uvicorn app.main:app --reload
```

Then open `http://localhost:8000`.

Without voice, straight from the terminal (fastest way to iterate on dialogue logic):

```powershell
python -m scripts.terminal_chat
```

### Test it

```powershell
pytest
```

125 tests, fully offline (mocked LLM responses and calendar fixtures - no network or real
credentials needed anywhere in the suite).

---

## Latency, across the stack changes

The stack changed twice over the course of this project - deploy target (local -> Render,
explored and abandoned -> GCP Cloud Run) and LLM provider (Gemini -> Claude). Render was replaced
*before* it was ever load-tested at the deployed level, so there's no real deployed-Render number
to put in this table - rather than invent one, here's what was actually measured, each one dated
and reproducible via the scripts named below:

| | Isolated LLM call | Isolated Calendar call | Isolated TTS first-byte | Full turn, live deployed |
|---|---|---|---|---|
| **Local dev + Gemini** (2026-07-26, pre-GCP) | 696-785 ms | 1212-2623 ms | 892-1025 ms | not deployed yet |
| **Local dev + Claude** (2026-07-27) | 1395-2039 ms | 1121-1166 ms | 771-780 ms | n/a (see next row) |
| **GCP Cloud Run + Claude** (2026-07-27, current) | - | - | - | 1751-2397 ms reply text, 2840-3688 ms full audio |
| **Render** (any provider) | never benchmarked - dropped before a deployed measurement existed | | | |

A fresh attempt to also re-measure Gemini's isolated call today hung indefinitely (100+ seconds,
no response) rather than returning a number - consistent with the free-tier quota exhaustion
documented in `app/config.py`, and itself a small live demonstration of why Claude is the
default now. Full numbers, methodology, and the reasoning behind each gap are in
`docs/latency.md`; the actual latency-reduction techniques applied (concurrent clause synthesis,
a perceived-latency filler, singleton clients, response caching) are in
[Voice pipeline](#voice-pipeline-stttts) below.

---

## Table of contents

1. [Core design principle](#core-design-principle)
2. [The 6 hard cases](#the-6-hard-cases-from-the-assignment-brief)
3. [Architecture](#architecture)
4. [The LLM layer, in depth](#the-llm-layer-in-depth)
5. [Deterministic overrides - when the dialogue layer doesn't trust the LLM](#deterministic-overrides---when-the-dialogue-layer-doesnt-trust-the-llm)
6. [Voice pipeline](#voice-pipeline-stttts)
7. [Testing strategy](#testing-strategy)
8. [Deployment](#deployment)
9. [Explored and rejected: Render](#explored-and-rejected-render)
10. [Known limitations](#known-limitations)
11. [Project structure](#project-structure)

---

## Core design principle

**The LLM only ever extracts raw fields from natural language - it never computes an actual
date, never decides whether a slot is free, and never writes the reply text.**

Every one of the 10 intent "kinds" in `app/schemas.py` carries only raw values a language model
can read straight off the sentence: a weekday name, an hour string, a day offset, an event name.
All arithmetic - "what date is the last weekday of this month," "is 30 minutes actually free
starting at 2pm," "what should I say out loud" - is plain, testable Python:

- **`app/dateresolve/`** turns raw fields into concrete datetimes. No LLM import exists anywhere
  in this package (this is grep-verifiable). It's also where a large amount of hard-earned
  defensive logic lives - rejecting an implausible parse rather than guessing, catching a
  resolved date that doesn't match a weekday the user actually said, normalizing "3 o'clock"
  (which `dateparser` can't parse at all), and so on. Every one of these was added because a real
  phrase broke something, and each has a regression test.
- **`app/scheduling/`** checks real Google Calendar freebusy data on a 30-minute grid and ranks
  candidates by closeness to the stated preference.
- **`app/dialogue/templates.py`** assembles the spoken reply from plain Python string templates -
  never from LLM-generated text. What gets said out loud is always grounded in a real computed
  time, never a hallucination.

This has a second consequence beyond correctness: several judgment calls that *look* like natural
language understanding are handled in deterministic code instead of asking an LLM to classify
them, because the LLM literally isn't given the information needed to get them right (see
[Deterministic overrides](#deterministic-overrides---when-the-dialogue-layer-doesnt-trust-the-llm)
below) - or because, empirically, it kept getting them wrong regardless of which model or how the
prompt was worded.

## The 6 hard cases from the assignment brief

| Case | Example | Schema kind | Resolver function |
|---|---|---|---|
| Deadline-driven | "45 minutes sometime before my flight Friday at 6 PM" | `deadline_before` | `_resolve_deadline_before` |
| Event-anchored | "a 15-minute chat a day or two after the Project Alpha Kick-off event" | `event_relative` | `_resolve_event_relative` |
| Calendar arithmetic | "1-hour meeting for the last weekday of this month" | `calendar_arithmetic` | `_resolve_calendar_arithmetic` |
| Vague/multi-constraint | "next week, not too early, not on Wednesday" | `relative_range_with_exclusions` | `_resolve_relative_range` |
| Contextual memory | "our usual sync-up" | `contextual_reference` | `resolve_contextual_duration` |
| Dynamic buffer | "evening, after 7, but I need an hour to decompress after my last meeting" | `dynamic_buffer` | `_resolve_dynamic_buffer` |

Plus 4 more kinds that aren't in the brief but are needed for a real conversation to work at all:
`simple_datetime` (the single most common thing a real user says - "Tuesday at 2pm"),
`duration_update` ("actually we need a full hour now"), `slot_decision` (confirming/rejecting/
picking among offered slots), and `out_of_scope` (anything that isn't a scheduling request -
without an explicit escape hatch, a forced-choice schema will coerce "what's the weather today"
into a fake booking attempt instead of recognizing it doesn't belong to any real kind).

### Why the schema looks the way it does

A few of these decisions took a wrong turn first and are worth explaining, since the *shape* of
the reasoning generalizes:

- **`calendar_arithmetic` started as a single enum** (`LAST_WEEKDAY_OF_MONTH`) and was
  decomposed into `ordinal` × `day_type` × `month_offset` after "first weekday of next month"
  came back confidently wrong (silently coerced to `LAST_WEEKDAY_OF_MONTH`, the only value that
  existed). The lesson that stuck: **a schema with only one representable answer produces a
  confidently wrong one instead of an error** when the user asks for something else. The same
  mistake had already happened once with `relative_range_with_exclusions.week_offset` (originally
  `Literal["this_week", "next_week"]`, generalized to a signed integer after "the week after
  next" made the model abandon the schema entirely and silently fall back to `simple_datetime`,
  losing `exclude_weekdays`/`time_preference` in the process, since that kind has neither field).
- **`earliest_time` lives on a `HasEarliestTime` mixin, not on the shared base.** It was tried on
  the base first (all 6 scheduling kinds) - a mistake, since 3 of those kinds' resolver functions
  never read it at all, so a constraint set there would be silently dropped. A field only belongs
  on the base every kind shares if every kind's resolver actually honors it.
- **`SimpleDateTime.raw_phrase` is `Optional[str]`, not a required `str`.** Required, it forced
  the model to choose between inventing text (violating the "never invent" rule) or
  misclassifying a legitimate-but-incomplete request ("I need to schedule a meeting" - the
  assignment's own example opening line) as `out_of_scope`. Confirmed live: 2/2 times it chose
  the latter. Making the field optional gives the model a legal way to say "nothing stated yet."
- **A `meeting_title` field was tried and reverted.** Extracting "with Rahul" as a real calendar
  event name sounded like a nice touch, but it destabilized real Gemini's structured output on
  the free-tier model - its own leaked reasoning showed it correctly computing the title
  internally, while the actual field still came back null and that reasoning text spilled into
  `raw_phrase` instead, corrupting date parsing. Not worth shipping an unreliable feature.

## Architecture

```
                                    ┌─────────────────┐
   browser mic / text  ──────────▶ │  app/ws/handler  │
                                    └────────┬─────────┘
                                             │ transcript
                                    ┌────────▼─────────┐
                                    │ dialogue/manager │◀── SessionState (per connection)
                                    └────────┬─────────┘
                    ┌───────────┬───────────┼────────────┬─────────────┐
                    ▼           ▼            ▼            ▼             ▼
              llm/client   dateresolve  scheduling/  calendar_client  dialogue/
             .extract_     .resolve()   slot_finder  (real freebusy/   templates
              intent()    (deterministic  + ranking    insert_event)  (reply text,
           (Claude/Gemini,   Python,                                  never LLM)
             structured      zero LLM)
              output)
                                             │
                                    ┌────────▼─────────┐
                                    │   tts/streamer    │──▶ clause-chunked audio back to browser
                                    └───────────────────┘
```

One `DialogueManager` per websocket connection (one conversation). Each turn: extract structured
intent → resolve it to concrete search windows → check real freebusy data → rank candidates →
assemble a reply from templates → stream TTS audio back, clause by clause.

### `app/schemas.py` - the intent shapes

The 10-kind discriminated union described above. This is the single contract both LLM backends
must fill in identically, and the only thing `app/dateresolve/` ever consumes.

### `app/dateresolve/` - deterministic resolution

`resolver.py` dispatches each kind to its own resolve function, decomposed into small named
helpers rather than one large function - e.g. `_resolve_simple_datetime` is really
`_reject_if_implausibly_long` → `normalize_oclock` → day-part extraction → `_parse_via_dateparser`
→ `_compute_window`, each independently testable. `helpers.py` holds the actual date arithmetic:
weekday resolution, day-part-to-hour-window mapping, week-qualifier handling ("next week on
Monday" is a genuinely different date than bare "Monday" - see the regression test
`test_next_week_qualifier_actually_shifts_a_week_not_just_nearest_occurrence`), business-hour
clamping, and so on.

A representative sample of the "found via real usage" fixes baked into this layer:
- `dateparser` cannot parse "next Wednesday 3:00", "next week on Tuesday", or "3 o'clock" at all
  (confirmed directly - returns `None`) - each has a dedicated deterministic path instead.
- A resolved date that doesn't match the weekday the user actually stated ("Wednesday 6" once
  resolved to a Saturday, with no error) is rejected outright rather than silently trusted.
- `raw_phrase` gets a length-based implausibility check (both word count *and* character count -
  word count alone missed a real corruption that was hyphen/colon-joined, not space-joined) before
  ever reaching `dateparser`, since a garbled phrase might otherwise resolve to *some* plausible-
  looking wrong date instead of failing cleanly.
- A multi-day search window's "not too early" preference is applied to *every* day in the range,
  not just the first/last (a naive first implementation only respected the edges).

### `app/scheduling/` - conflict detection + ranking

`slot_finder.py` walks a 30-minute grid across each search window and checks each candidate
against one real `freebusy.query` call per window (not per candidate - cheaper and lower
latency), naturally skipping any grid point that overlaps a busy period. If a meeting occupies
11:00-11:30, candidates simply jump from 10:30 straight to 11:30 - this isn't special-cased, it
falls out of the grid-walk-and-filter design for free. If the original window has zero free
slots, it widens by 7 days and retries once rather than presenting a dead end. `ranking.py` then
scores the survivors by closeness to the stated preference, so the answer is demonstrably not
just "the first chronological slot."

### `app/dialogue/` - orchestration + replies

`manager.py` is the state machine: decide whether there's enough information to search yet,
carry over anything already established across a mid-conversation correction, and route to the
right handler based on what kind of thing the user just said. `templates.py` assembles the
spoken reply as a list of clauses (not one string) - see [Voice pipeline](#voice-pipeline-stttts)
for why that shape matters for latency.

### `app/calendar_client/` - Google Calendar

Thin wrappers around `freebusy.query` / `events.list` / `events.insert` / `events.delete`.
`auth.py` builds OAuth credentials purely from settings (never reads a local `token.json`) - the
deployed container's filesystem is ephemeral, so it reconstructs credentials from env vars at
startup every time, which is also exactly what `scripts/sanity/check_oauth_env_reconstruction.py`
verifies directly.

## The LLM layer, in depth

Two backends implement the identical `extract_intent(transcript, condensed_state) ->
TemporalExpression` contract and are swappable via `LLM_PROVIDER`, dispatched from the one-page
`app/llm/client.py`:

### `anthropic` (default) - `app/llm/anthropic_backend.py`

Claude Haiku 4.5 via tool-use, with **one tool per intent kind** rather than one flat tool
exposing every field from every kind. This matters: a first prototype using a single flat
schema let the model put fields on the wrong kind entirely (e.g. `anchor_weekday`/
`time_preference`, which belong to other kinds, showing up on `simple_datetime`) because nothing
in the schema said those fields didn't belong there. Splitting into 10 per-kind tools with
`tool_choice="any"` (forces exactly one tool call, model's choice which) mirrors what Gemini's
`response_schema` enforces natively via a real discriminated union, and fixed the cross-kind
confusion completely.

The system prompt is deliberately short - a handful of cross-cutting distinctions that no single
tool's own `description` can carry (the `simple_datetime` vs `relative_range` boundary,
`out_of_scope` vs an incomplete-but-real request), not a "tool choice guide" restating what each
tool already says about itself, and no exhaustive worked examples. Tested clean without the
Gemini-style hand-holding that turned out to be necessary for the other backend.

### `gemini` (fallback) - `app/llm/gemini_backend.py`

Gemini via native structured output (`response_schema`), kept available via
`LLM_PROVIDER=gemini` in case Anthropic pricing/quota ever becomes a blocker. This backend
carries several regex-based safety nets that exist specifically because
`gemini-flash-lite-latest` (the only Gemini model with usable free-tier quota - see below)
turned out to have real, reproducible failure modes a stronger model didn't share:

- **Leading-duration extraction** ("a 30 minute meeting Tuesday at 2pm") failed 9/9 times even
  after adding explicit prompt examples for exactly this pattern - proof prompting alone wasn't
  going to fix it. A regex now recovers `duration_minutes` from the raw transcript whenever the
  model leaves it null.
- **Duration text leaking into `raw_phrase`** even when `duration_minutes` was extracted
  correctly ("tomorrow at 3pm for 30 minutes" → `raw_phrase` unchanged, breaking `dateparser` on
  the trailing "for 30 minutes"). A shared cleanup (`app/llm/text_cleanup.py`, used by *both*
  backends - see below) strips it.
- **Occasional severe generative corruption** - not just leaked reasoning, but hallucinated
  UUIDs and ISO timestamps appended to an otherwise-clean phrase. The corruption-detection check
  in both this backend and `resolver.py`'s last-resort safety net checks character length
  *alongside* word count, since a real observed corruption was only 6 whitespace-separated
  "words" (hyphen/colon-joined, not space-joined) despite being obvious garbage - and this
  particular shape is dangerous specifically because `dateparser` is *good* at parsing
  ISO-looking fragments, risking a confidently wrong date instead of a clean failure.
- A reconstruction fallback rebuilds `raw_phrase` from the original transcript by cutting at the
  first recognizable date/time keyword, discarding whatever precedes it (a command verb like
  "Book"/"Schedule", leaked garbage, anything) - because most real basic queries open with a
  command verb, and a naive "strip one specific duration-shaped prefix" approach didn't
  generalize to that.

`app/llm/text_cleanup.py` holds the one fix that's shared by both backends rather than scoped to
Gemini's file: stripping duration text that leaked into `raw_phrase` alongside an already-correct
`duration_minutes`. Even Claude does this occasionally (roughly 1 in 5-10 calls in spot testing,
only reproduced with a populated session-state context) - far rarer than Gemini, but the fix is
cheap and provably safe (it only ever removes text redundant with a value already known), so
there's no reason to withhold it just because one backend needs it less.

### Why Claude over Gemini

Side-by-side testing (2026-07-27) on the exact phrases that broke Gemini this project's session:
Claude Haiku 4.5 scored 18/18 clean across a combined battery (10 basic single/multi-turn
scenarios + 8 covering all 6 assignment hard cases), including every one of Gemini's worst
failures, with zero regex fallbacks needed for the serious ones. Cost is roughly $0.003/call
(~$3 per 1000 full conversations) on Haiku 4.5's pricing - negligible for this project's scale.

Gemini's free tier also turned out to have a real, undocumented **20-requests-per-day** cap on
both `gemini-3.5-flash` and `gemini-3.6-flash` (not the ~1500 RPD commonly quoted elsewhere for
Gemini's free tier) - confirmed by waiting out the API's own reported `retryDelay`, then waiting
100+ seconds completely untouched, and still getting `429`s both times, on both models, on
different days. `gemini-flash-lite-latest` was the only Gemini model with consistently usable
free-tier quota, which is why it's still the default if `LLM_PROVIDER=gemini` is chosen.

## Deterministic overrides - when the dialogue layer doesn't trust the LLM

A recurring pattern in `app/dialogue/manager.py`: certain judgment calls are made in plain Python
*before* the LLM is ever consulted, or by post-processing what it returns, rather than trusting
either model to classify them. Two different reasons drive this, and they're worth telling apart:

**Reason 1 - the LLM is never even given the information needed.** To keep token usage flat
regardless of conversation length, the condensed session state sent to the LLM includes
`num_offered_candidates` (a count) but never the offered candidates' actual clock times. So when
a user states an explicit new time while slots are being offered ("book it for 12:00 p.m." when
9:00/9:30/10:00 AM were offered), matching that stated time against the real offered candidates
*has* to happen in code - no LLM, however good, could get this right without also being handed
the exact candidate times, which would cost tokens on every single turn. This is what
`_resolve_explicit_time_during_confirmation` and `_parse_slot_selection` do, checked before
`extract_intent()` is ever called during the `confirming` phase. (Historical note: this was
originally motivated by Gemini classifying this case wrong ~2 times out of 3 despite explicit
prompt instructions - but even after moving to Claude, the fix stayed, because the *reason* it
belongs in code has nothing to do with model quality.)

**Reason 2 - state-merging that no LLM call is asked to do.** Two bugs found via live testing
(2026-07-27) both had the same shape: the user's message only *partially* corrected an
established multi-field constraint, and the LLM's fresh classification silently discarded the
rest of it.

- *"Before my flight Friday at 6pm" → offers Monday slots → "no, I want it on Friday only"* was
  being reclassified as a bare `simple_datetime` for "Friday", discarding the 6pm deadline
  entirely. It only *looked* correct in testing because ranking happens to prefer morning slots
  anyway, which coincidentally land before 6pm regardless of whether the deadline was actually
  honored - confirmed wrong by inspecting the actual extracted intent, not just the offered
  times. Fixed by `_try_merge_bare_day_correction`: when the established constraint is a
  `DeadlineBefore` and the new message is *just* a bare weekday name with nothing else stated,
  merge it into the existing deadline (keeping `anchor_time`/`buffer_minutes`/`earliest_time`)
  instead of replacing the whole constraint with a poorer one.
- *"a 30 minute meeting Friday afternoon" → offers afternoon slots → "any other options,
  something later in the day"* was being classified as a bare slot rejection, and the reply asked
  a generic "what day or time would work better?" - technically not wrong, but it discarded a
  preference the user had already stated in the same breath. Fixed by
  `_try_apply_rejection_hint`: detects an explicit day-part word or a relative "later"/"earlier"
  nudge (shifted against whatever day-part was last searched - "later" from "afternoon" becomes
  "evening") in the rejection message, and re-searches with it applied.

Both are scoped narrowly to the exact reproduced patterns (a bare weekday correction against a
`DeadlineBefore`; a day-part hint against a `SimpleDateTime`) rather than generalized speculatively
to every kind - `event_relative`/`dynamic_buffer` don't have a single anchor weekday to correct
the same way, so the same trick doesn't obviously apply there yet.

## Voice pipeline (STT/TTS)

**STT**: the browser's Web Speech API is the primary path (zero server cost, transcribes
client-side). If unavailable (e.g. Firefox), the browser falls back to `MediaRecorder`, streaming
a webm/opus blob to the server, where `faster-whisper` transcribes it - lazily loaded only on
first actual use, to keep idle memory low when the fallback never triggers.

**TTS**: `edge-tts` is primary; `pyttsx3` (offline, `espeak-ng`-backed on Linux) is the fallback
if it fails, since `edge-tts` is an unofficial, reverse-engineered client with no SLA. A boot-time
health check logs which engine actually responded.

**Why replies are clause-lists, not single strings**: since replies are template-assembled (never
LLM-generated), there's no free-form token stream to tap for the "streaming" differentiator the
assignment brief describes. Clause boundaries are the natural chunk unit instead - `tts/streamer.py`
synthesizes all of a reply's clauses concurrently (not sequentially) and delivers them back to the
browser in order as each becomes ready, so a 3-clause reply's total synthesis time collapses
toward the slowest single clause instead of the sum of all three.

### Latency-reduction techniques actually implemented

800ms end-to-end was never realistically achievable on this stack (see the
[latency table](#latency-across-the-stack-changes) above and `docs/latency.md`), so the actual
work went into closing the gap between real and *felt* latency, plus trimming what's cheap to
trim:

- **Perceived-latency mask** - a short filler clause ("Let me check that...") is synthesized
  once at server startup (pre-warmed, so not even the very first real turn pays for it) and sent
  immediately, while the real LLM/resolve/calendar work runs in the background. This is the
  single biggest lever: the table above shows filler audio arriving in under 20ms on every real
  measurement, regardless of how long the actual turn takes.
- **Concurrent TTS clause synthesis** (`tts/streamer.py`) - all of a reply's clauses start
  synthesizing at the same time via `asyncio.create_task`, not one after another; only *delivery*
  order is preserved, not synthesis order. A 3-clause reply's total synthesis time collapses
  toward the slowest single clause instead of the sum of all three.
- **In-memory TTS clause cache** (`tts/streamer.py`) - exact boilerplate clauses repeated across
  many turns ("Should I book it?", "Anything else?") are cached after first synthesis, so a real
  `edge-tts` network call only happens once per distinct clause, not once per turn.
- **Singleton LLM clients** (`gemini_backend.py`/`anthropic_backend.py`'s `_get_client()`) - built
  once per process at first use, not reconstructed per request, avoiding a repeated TLS/auth
  handshake on every single turn.
- **Per-conversation Calendar lookup cache** (`dialogue/manager.py`'s `_event_cache`/
  `_last_meeting_cache`) - if a turn already resolved a named event or "last meeting today" via a
  real Calendar API call, a later turn in the *same* conversation (e.g. a duration-only
  correction that re-triggers `resolve()`) reuses that result instead of querying again.
- **One freebusy query per search window, not per candidate slot** (`scheduling/slot_finder.py`)
  - candidates are generated and filtered against a single real `freebusy.query` response per
    window, rather than checking each 30-minute grid point with its own API call.

Not implemented, and worth naming honestly: batching multiple *different* Calendar API calls
(e.g. an event lookup and a freebusy check in the same turn) into one `BatchHttpRequest` was in
the original project plan's latency backlog, but never actually built - the caching techniques
above turned out to cover the cases that came up in practice.

## Testing strategy

`pytest` runs 125 tests, fully offline - mocked LLM responses (`app/llm/mock_responses.py`,
keyed by phrase) and fixture Calendar API data (`tests/fixtures/calendar_fixtures.py`), no real
network or credentials needed anywhere in the suite. Every one of the 6 hard cases has a named
test module; conflict resolution, mid-conversation state changes, and both deterministic
overrides described above each have their own dedicated file.

That's deliberately not the whole story, though - **the majority of real bugs in this project
were found by testing against the real LLM and the real deployed service**, not the mocked suite,
since the mock responses encode "what the LLM is *supposed* to return," not what it actually
returns under real conditions. `scripts/dev/` and `scripts/sanity/` hold small standalone scripts
for exactly this: a single real LLM call, real OAuth, a real booking + independent re-read +
cleanup, real TTS engine health, and so on - each script's docstring says exactly what it
verifies and what it costs. The general pattern that found nearly every bug documented in this
README: construct the exact reported phrase, run it against the real backend in isolation, look
at the *actual* extracted intent (not just the final reply text, which can look correct by
coincidence), and only then decide whether the fix belongs in the prompt, the schema, or
deterministic code.

## Deployment

Deployed on **Google Cloud Run** (`asia-south2`) via the repo's `Dockerfile` - `espeak-ng` is
installed explicitly at build time (needed by the `pyttsx3` TTS fallback), which is also why this
project deploys via a Dockerfile rather than a native buildpack. Environment variables (API keys,
model/provider selection, OAuth credentials) are set directly on the Cloud Run service; the
container's filesystem is ephemeral, so nothing here is read from a local file at runtime.

Real per-hop latency measurements (isolated, not simulated) live in `docs/latency.md`.

## Explored and rejected: Render

An earlier phase of this project deployed to **Render** instead of GCP Cloud Run, and the
Dockerfile-over-buildpack decision actually originated there (Render's native Python buildpack
doesn't reliably allow the `apt-get install espeak-ng` step this project needs). Render was later
dropped in favor of GCP Cloud Run - `render.yaml` and a `scripts/hello_render/` throwaway
hello-world app (used only to validate that first deploy path) were both removed once the GCP
deployment replaced it. The Dockerfile itself didn't need to change for the switch, since both
platforms build from the same container image - the decision was about where to run it, not how
to build it.

## Known limitations

- **Single-user calendar.** Not multi-tenant - one hardcoded OAuth refresh token (the deployer's
  own calendar), shared by every visitor to the deployed URL. A deliberate scope decision for
  this stage, not an oversight - multi-tenant Google sign-in remains a planned-but-not-started
  phase.
- **OAuth refresh token can expire.** Apps still in OAuth "Testing" publish status get a ~7-day
  refresh token lifetime. If calendar reads/writes start failing with an auth error, re-run
  `scripts/oauth_bootstrap.py` and update the deployed env var.
- **The 800ms end-to-end latency target isn't realistically met** on this stack - see
  `docs/latency.md` for real measured numbers per hop and the perceived-latency mask used to
  soften it.
- **A day-part rejection hint only re-searches for `SimpleDateTime`-established constraints,
  and a bare-weekday deadline correction only merges into `DeadlineBefore`.** Both fixes are
  scoped to the exact patterns that were actually reproduced via live testing rather than
  generalized speculatively to every intent kind - see
  [Deterministic overrides](#deterministic-overrides---when-the-dialogue-layer-doesnt-trust-the-llm).
- **`deadline_before` has no concept of "restrict to just the deadline day."** Its search window
  is always "anytime between now and the deadline," by design - so a later message narrowing to
  the deadline's own day (rather than correcting which day the deadline falls on) won't shrink
  the search window to just that day, even though the deadline time itself is now correctly
  preserved across the correction.

## Project structure

```
app/
  main.py, config.py, state.py, schemas.py   - app entrypoint, settings, session state, intent schema
  llm/
    client.py            - provider dispatch (LLM_PROVIDER env var)
    anthropic_backend.py  - Claude Haiku 4.5, one tool per intent kind (default)
    gemini_backend.py     - Gemini structured output + regex safety nets (fallback)
    text_cleanup.py       - the one fix shared by both backends
    prompts.py            - Gemini's system prompt
    errors.py             - LLMExtractionError, shared to avoid a circular import
    mock_responses.py     - canned responses for USE_MOCK_LLM=true
  dateresolve/            - deterministic date/time resolution, zero LLM involvement (grep-verifiable)
  calendar_client/        - Google Calendar API wrappers + OAuth
  scheduling/             - conflict detection (slot_finder.py) + ranking
  dialogue/
    manager.py            - conversation state machine + deterministic overrides
    templates.py          - reply assembly, plain Python, never an LLM call
  stt/                    - faster-whisper fallback wrapper
  tts/                    - edge-tts + pyttsx3 fallback, clause-chunked streaming
  ws/                     - websocket protocol + handler
  persistence.py          - JSON-file-backed "usual meeting" defaults
static/                   - browser frontend (mic capture, playback)
scripts/
  oauth_bootstrap.py      - one-time local OAuth consent flow
  terminal_chat.py        - text-only REPL against DialogueManager
  bench_latency.py        - isolated per-hop latency measurement
  dev/                    - manual verification scripts (real LLM/booking/TTS checks)
  sanity/                 - smaller standalone sanity checks
tests/                    - pytest suite, fully offline
docs/latency.md           - real measured per-hop latency numbers
```

## License

MIT - see [LICENSE](LICENSE).
