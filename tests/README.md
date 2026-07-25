# Test suite notes

All automated tests in this directory run fully offline - no network, no real Google/Gemini
credentials required (verified by running with `.env` renamed aside, see the main README). They
use `USE_MOCK_LLM=true` and fixture-based calendar callbacks (`tests/fixtures/calendar_fixtures.py`)
or a mocked Google API service object (`unittest.mock`), never the real network.

Coverage on the deterministic core (`app/dateresolve`, `app/scheduling`) is 92% - the remaining
gaps are mostly defensive branches for states that shouldn't occur if the dialogue layer behaves
correctly (e.g. calling `resolve()` directly with a bare `ContextualReference`), plus the
`"not_too_late"` preference direction, which is implemented but has no test since the assignment's
own examples only exercise `"not_too_early"`.

## Manual integration checklist (not part of automated CI)

Mocks prove our code handles a shape of data we made up - they don't prove it handles the real
Google Calendar API. All 6 official scenarios have been run end-to-end against a real calendar at
least once (see `scripts/dev/try_real_all_scenarios.py` and its commit history), each booked,
independently re-read to confirm, then deleted. That script isn't part of automated CI since it
writes to a real calendar; re-run it by hand to re-verify:

```
python -m scripts.dev.try_real_all_scenarios
```

It seeds 2 real events first (`Project Alpha Kick-off`, an evening meeting ending today), runs
all 6 scenarios, and cleans up everything it creates afterward (including the seed events) even
if a scenario fails partway through.

If you'd rather check just the two scenarios that need a real seeded event, by hand:

### Event-anchored ("a day or two after the Project Alpha Kick-off event")

1. Create a real event named exactly `Project Alpha Kick-off` on your calendar, a few days out.
2. Run a turn with *"a 15-minute chat a day or two after the Project Alpha Kick-off event"*.
3. Confirm the proposed window falls 1-2 days after the real event's end time.
4. Delete the test event afterward if you don't want it lingering.

### Dynamic buffer ("evening, after 7, need an hour to decompress after my last meeting")

1. Create a real event on your calendar ending after 6pm today (e.g. 6:45pm-7:00pm).
2. Run a turn with *"evening, after 7, but I need an hour to decompress after my last meeting"*.
3. Confirm the offered slot starts at (that event's end + 60 minutes), not just at 7pm - proving
   the buffer came from the real "last meeting of the day" lookup, not a guess.
4. Delete the test event afterward.
