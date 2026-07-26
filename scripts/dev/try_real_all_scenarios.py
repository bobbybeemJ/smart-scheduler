"""Real-everything verification for all 6 assignment scenarios: real Gemini extraction, real
Calendar reads/writes, no mocking anywhere in this script. Also proves the caching optimization
actually avoids redundant real API calls (not just in a fixture-based unit test) by counting real
find_event_by_name/find_last_event_of_day invocations through a multi-turn conversation.

Seeds 2 real events (Project Alpha Kick-off, an evening meeting for the dynamic-buffer case),
then runs each of the 6 scenarios through the real DialogueManager, books whatever slot is
offered, independently re-reads it by its exact event id to confirm, then deletes it. Cleans up
the 2 seed events too. Event ids are tracked for cleanup *before* any assertion runs, and
verification is always by exact event id (calendar_client.get_event), never name+time-range
search - both fixed after an earlier version of this script left orphaned events behind and then
confused itself by matching the wrong same-named event nearby.

Run: python -m scripts.dev.try_real_all_scenarios
"""

import datetime as dt

from dotenv import load_dotenv

load_dotenv()

from app import config  # noqa: E402

config.settings.use_mock_llm = False  # real Gemini, unconditionally - this script is the real-everything check

from app.calendar_client import client as calendar_client  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402

created_ids: list[str] = []


def make_manager():
    """Wraps the real find_event/find_last_meeting with counters (still calling the real
    functions underneath) so caching can be proven against genuine API call counts, not a
    fixture proxy standing in for them."""
    captured: dict = {}
    call_counts = {"find_event": 0, "find_last_meeting": 0}

    def counting_find_event(name):
        call_counts["find_event"] += 1
        return calendar_client.find_event_by_name(name)

    def counting_find_last_meeting(day):
        call_counts["find_last_meeting"] += 1
        return calendar_client.find_last_event_of_day(day)

    def capturing_insert_event(summary, start, end):
        result = calendar_client.insert_event(summary, start, end)
        captured["event"] = result
        created_ids.append(result["id"])  # tracked immediately - before any assertion can fail
        return result

    manager = DialogueManager(
        find_event=counting_find_event,
        find_last_meeting=counting_find_last_meeting,
        freebusy_fn=calendar_client.freebusy,
        insert_event_fn=capturing_insert_event,
    )
    return manager, captured, call_counts


def _print_timing(manager: DialogueManager) -> None:
    t = manager.last_turn_timing
    if t is None:
        return
    print(f"    [timing] llm={t.llm_ms:.0f}ms resolve={t.resolve_ms:.0f}ms calendar={t.calendar_ms:.0f}ms")


def _book_and_verify(manager: DialogueManager, captured: dict, offered) -> None:
    reply = manager.handle_turn("yes, book it")
    print(f"> yes, book it\n  bot: {' '.join(reply)}")
    _print_timing(manager)

    if manager.state.phase != "booked":
        print("  RESULT: booking did not complete.")
        return

    event_id = captured["event"]["id"]
    found = calendar_client.get_event(event_id)  # unambiguous - looks up this exact event
    assert found["start"] == offered.start, f"expected {offered.start}, real calendar has {found['start']}"
    print(f"  RESULT: OK - booked and independently re-read at {found['start']}, event id={event_id}")

    calendar_client.delete_event(event_id)
    created_ids.remove(event_id)
    print("  Cleaned up: event deleted.")


def run_scenario(name: str, turns: list[str], check_cache_calls: str = None) -> None:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    manager, captured, call_counts = make_manager()
    reply = []
    for turn in turns:
        print(f"> {turn}")
        reply = manager.handle_turn(turn)
        print(f"  bot: {' '.join(reply)}")
        _print_timing(manager)
        if check_cache_calls:
            print(f"    [real API calls so far] find_event={call_counts['find_event']} find_last_meeting={call_counts['find_last_meeting']}")

    if check_cache_calls:
        count = call_counts[check_cache_calls]
        assert count == 1, (
            f"expected exactly 1 real {check_cache_calls} call across {len(turns)} turns "
            f"(caching should prevent re-querying the same lookup), got {count}"
        )
        print(f"  CACHE CHECK: OK - {check_cache_calls} was only called once (real API) across {len(turns)} turns, despite {len(turns)} resolve() calls")

    if manager.state.phase != "confirming" or manager.state.top_candidate is None:
        print(f"  RESULT: no bookable slot offered (phase={manager.state.phase}) - real freebusy was still checked.")
        return

    offered = manager.state.top_candidate
    _book_and_verify(manager, captured, offered)


def main():
    now = dt.datetime.now()
    kickoff_start = now - dt.timedelta(days=2)
    kickoff_start = kickoff_start.replace(hour=10, minute=0, second=0, microsecond=0)
    kickoff_end = kickoff_start + dt.timedelta(hours=1)

    evening_meeting_start = now.replace(hour=18, minute=45, second=0, microsecond=0)
    evening_meeting_end = now.replace(hour=19, minute=0, second=0, microsecond=0)

    print("Seeding 2 real events for the scenarios that need them...")
    kickoff_event = calendar_client.insert_event("Project Alpha Kick-off", kickoff_start, kickoff_end)
    evening_event = calendar_client.insert_event("Last meeting today (seed for dynamic buffer test)", evening_meeting_start, evening_meeting_end)
    print(f"  Project Alpha Kick-off: {kickoff_start} -> {kickoff_end} (id={kickoff_event['id']})")
    print(f"  Evening meeting: {evening_meeting_start} -> {evening_meeting_end} (id={evening_event['id']})")

    try:
        run_scenario("1. Deadline-driven", ["45 minutes sometime before my flight Friday at 6 PM"])

        run_scenario(
            "2. Event-anchored (multi-turn - proving find_event is cached across duration changes)",
            [
                "a 15-minute chat a day or two after the Project Alpha Kick-off event",
                "actually we need a full hour now",
                "actually, make it 20 minutes instead",
            ],
            check_cache_calls="find_event",
        )

        run_scenario("3. Calendar-arithmetic", ["1-hour meeting for the last weekday of this month"])

        run_scenario(
            "4. Vague/multi-constraint",
            ["next week, not too early, not on Wednesday", "actually we need a full hour now"],
        )

        manager, captured, call_counts = make_manager()
        manager.state.usual_meeting_defaults["usual sync-up"] = 30
        print(f"\n{'=' * 70}\n5. Contextual memory\n{'=' * 70}")
        print("> next week, not too early, not on Wednesday")
        print(f"  bot: {' '.join(manager.handle_turn('next week, not too early, not on Wednesday'))}")
        _print_timing(manager)
        print("> our usual sync-up")
        reply = manager.handle_turn("our usual sync-up")
        print(f"  bot: {' '.join(reply)}")
        _print_timing(manager)
        if manager.state.phase == "confirming" and manager.state.top_candidate is not None:
            offered = manager.state.top_candidate
            _book_and_verify(manager, captured, offered)
        else:
            print(f"  RESULT: no bookable slot offered (phase={manager.state.phase})")

        run_scenario(
            "6. Dynamic buffer (multi-turn - proving find_last_meeting is cached across duration changes)",
            [
                "evening, after 7, but I need an hour to decompress after my last meeting",
                "actually we need a full hour now",
                "actually, make it 45 minutes instead",
            ],
            check_cache_calls="find_last_meeting",
        )

    finally:
        print(f"\n{'=' * 70}\nCleanup\n{'=' * 70}")
        for event_id in list(created_ids):
            print(f"  Deleting leftover event {event_id} (a scenario above didn't clean up its own)")
            calendar_client.delete_event(event_id)
        calendar_client.delete_event(kickoff_event["id"])
        calendar_client.delete_event(evening_event["id"])
        print("  Seed events deleted.")


if __name__ == "__main__":
    main()
