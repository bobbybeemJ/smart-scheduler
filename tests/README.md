# Test suite notes

All automated tests in this directory run fully offline - no network, no real Google/Gemini
credentials required (verified by running with those env vars unset, see the main README). They
use `USE_MOCK_LLM=true` and fixture-based calendar callbacks (`tests/fixtures/calendar_fixtures.py`)
or a mocked Google API service object (`unittest.mock`), never the real network.

## Manual integration checklist (not part of automated CI)

Two of the six hard scenarios are also exercised against a **real** Google Calendar during
development (not on every test run, since that would make CI depend on live external state).
If you want to re-verify these by hand:

### Event-anchored ("a day or two after the Project Alpha Kick-off event")

1. Create a real event named exactly `Project Alpha Kick-off` on your calendar, a few days out.
2. Run `python -m scripts.dev.try_real_booking`-style flow (or a terminal chat session) with a
   phrase like *"a 15-minute chat a day or two after the Project Alpha Kick-off event"*.
3. Confirm the proposed window falls 1-2 days after the real event's end time.
4. Delete the test event afterward if you don't want it lingering.

### Dynamic buffer ("evening, after 7, need an hour to decompress after my last meeting")

1. Create a real event on your calendar ending after 6pm today (e.g. 6:45pm-7:00pm).
2. Run a turn with *"evening, after 7, but I need an hour to decompress after my last meeting"*.
3. Confirm the offered slot starts at (that event's end + 60 minutes), not just at 7pm - proving
   the buffer came from the real "last meeting of the day" lookup, not a guess.
4. Delete the test event afterward.

Both were verified this way during Phase 1/7 development (see the relevant commit messages) and
are covered automatically the rest of the time via `tests/fixtures/calendar_fixtures.py`, which
simulates the same real-lookup shapes without hitting the network.
