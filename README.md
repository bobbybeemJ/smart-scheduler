# NxD Smart Scheduler

A voice-enabled scheduling assistant backed by a real Google Calendar. It holds a multi-turn
conversation, resolves natural-language date/time expressions - including several genuinely hard
cases (deadline-driven, event-anchored, calendar arithmetic, vague multi-constraint ranges,
contextual "our usual sync-up" memory, and dynamic buffers relative to another meeting) - checks
real calendar availability, and books the slot.

**Live demo**: https://smart-scheduler-693682345402.asia-south2.run.app

## Design principle

The LLM only ever extracts *raw fields* from what the user said (a weekday name, an hour string,
a day offset, an event name) into a structured schema - it never computes an actual date, never
decides whether a slot is free, and never writes the reply text. All of that is deterministic
Python:

- `app/dateresolve/` turns raw fields into concrete datetimes (dateparser + hand-written weekday/
  calendar-arithmetic logic), with a large set of defensive checks earned through real testing -
  rejecting an implausible parse rather than guessing, catching a resolved date that doesn't match
  a weekday the user actually said, and so on.
- `app/scheduling/` checks real Google Calendar freebusy data and ranks candidate slots by
  closeness to the stated preference.
- `app/dialogue/templates.py` assembles the spoken reply from plain Python string templates -
  never from LLM-generated text, so what gets said out loud is always grounded in a real computed
  time, not a hallucination.

The dialogue manager (`app/dialogue/manager.py`) also intercepts certain judgment calls
deterministically rather than trusting the LLM with them - e.g. matching a newly-stated time
against already-offered slots during confirmation. This isn't just a workaround for a weaker
model: the LLM is never even given the offered slots' exact times (kept out of its context to
keep token usage flat), so that comparison has to happen in code regardless of which model is
answering.

## LLM backend

Two backends implement the same `extract_intent()` contract and are swappable via
`LLM_PROVIDER`:

- **`anthropic` (default)** - Claude Haiku 4.5 via tool-use, one tool per intent kind so each
  tool's schema only exposes the fields that kind actually has (mirrors a discriminated union).
  See `app/llm/anthropic_backend.py`.
- **`gemini`** - Gemini via structured output (`response_schema`), kept as a fallback option in
  case Anthropic pricing/quota ever becomes a blocker. See `app/llm/gemini_backend.py`, which
  also carries several regex-based safety nets for failure modes specific to the free-tier
  `gemini-flash-lite-latest` model (documented in that file).

## Setup

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Fill in `.env`:

- `ANTHROPIC_API_KEY` (or `GEMINI_API_KEY` if using `LLM_PROVIDER=gemini`) - set `USE_MOCK_LLM=false`
  to actually call it; `true` runs entirely offline against canned mock responses.
- Google OAuth credentials - run `python -m scripts.oauth_bootstrap` once (needs a GCP project
  with the Calendar API enabled and an OAuth consent screen in Testing status with your account
  added as a test user), then copy the printed values into `.env`.

Run the full pipeline locally:

```powershell
uvicorn app.main:app --reload
```

Or iterate without voice, straight from the terminal:

```powershell
python -m scripts.terminal_chat
```

## Testing

```powershell
pytest
```

119 tests, fully offline (mocked LLM responses and calendar fixtures, no network or real
credentials needed). `scripts/dev/` and `scripts/sanity/` hold small standalone scripts for
exercising individual pieces (a single real LLM call, real OAuth, real TTS, etc.) - each one's
docstring says exactly what it checks and what it costs.

## Known limitations

- **Single-user calendar.** Not multi-tenant - one hardcoded OAuth refresh token (the deployer's
  own calendar), shared by every visitor to the deployed URL. A deliberate scope decision for
  this stage, not an oversight.
- **OAuth refresh token can expire.** Apps still in OAuth "Testing" publish status get a ~7-day
  refresh token lifetime. If calendar reads/writes start failing with an auth error, re-run
  `scripts/oauth_bootstrap.py` and update the deployed env var.
- **The 800ms end-to-end latency target isn't realistically met** on this stack - see
  `docs/latency.md` for real measured numbers per hop and the perceived-latency mask used to
  soften it (an immediate filler reply while the real work runs).
- **Narrowing an established deadline/event-anchored request by day alone can drop the other
  constraint.** E.g. after "30 minutes before my flight Friday at 6pm" offers Monday slots,
  replying "no, Friday only" correctly narrows to Friday but can silently lose the 6pm deadline,
  since the model reclassifies the correction as a bare day rather than re-stating the full
  deadline context. Found via live testing 2026-07-27; not yet fixed - needs either state-merging
  logic aware of which established constraint is being partially corrected, or a more reliable
  way to get the model to reconstruct the rest of the original constraint from session context.
- Rejecting offered slots with a stated preference for what would work instead ("none of those
  work, how about the afternoon") is acknowledged but the preference itself isn't acted on yet -
  the system just asks again rather than re-searching with that hint.

## Project structure

```
app/
  main.py, config.py, state.py, schemas.py   - app entrypoint, settings, session state, intent schema
  llm/                                       - LLM backends (anthropic_backend.py, gemini_backend.py) + dispatcher
  dateresolve/                               - deterministic date/time resolution, zero LLM involvement
  calendar_client/                           - Google Calendar API wrappers + OAuth
  scheduling/                                - conflict detection + slot ranking
  dialogue/                                  - conversation orchestration + reply templates
  stt/, tts/                                 - speech-to-text fallback, text-to-speech streaming
  ws/                                        - websocket protocol + handler
static/                                      - browser frontend (mic capture, playback)
scripts/                                     - setup, sanity checks, and manual verification tools
tests/                                       - pytest suite, fully offline
docs/latency.md                              - real measured per-hop latency numbers
```
