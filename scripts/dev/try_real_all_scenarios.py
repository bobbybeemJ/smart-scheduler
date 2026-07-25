"""Real-calendar verification for all 6 assignment scenarios (requested after a fair challenge:
mocks only prove our code handles a shape of data we made up, not that it handles the real API).

Seeds 2 real events (Project Alpha Kick-off, an evening meeting for the dynamic-buffer case),
then runs each of the 6 scenarios through the real DialogueManager (real freebusy, real
find_event/find_last_meeting, real insert_event), books whatever slot is offered, independently
re-reads it by its exact event id to confirm, then deletes it. Cleans up the 2 seed events too.

Verification is by exact event id (calendar_client.get_event), not name+time-range search - an
earlier version of this script used a narrow time-range + name search instead, and a leftover
orphaned event from an earlier failed run caused it to verify against the WRONG same-named event
sitting nearby. Event ids are also tracked for cleanup *before* any assertion runs, so a failed
assertion can never again leave an untracked real event behind - that's exactly what created the
orphan in the first place.

By default uses the mock LLM (zero Gemini cost). Pass --real-llm to use real Gemini extraction
for every turn too, closing the gap where only the Calendar side had been proven against the
real API in the same run.

Run: python -m scripts.dev.try_real_all_scenarios [--real-llm]
"""

import datetime as dt
import sys

from dotenv import load_dotenv

load_dotenv()

from app import config  # noqa: E402

config.settings.use_mock_llm = "--real-llm" not in sys.argv

from app.calendar_client import client as calendar_client  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402

created_ids: list[str] = []


def make_manager() -> DialogueManager:
    captured: dict = {}

    def capturing_insert_event(summary, start, end):
        result = calendar_client.insert_event(summary, start, end)
        captured["event"] = result
        created_ids.append(result["id"])  # tracked immediately - before any assertion can fail
        return result

    manager = DialogueManager(
        find_event=calendar_client.find_event_by_name,
        find_last_meeting=calendar_client.find_last_event_of_day,
        freebusy_fn=calendar_client.freebusy,
        insert_event_fn=capturing_insert_event,
    )
    return manager, captured


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


def run_scenario(name: str, turns: list[str]) -> None:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    manager, captured = make_manager()
    reply = []
    for turn in turns:
        print(f"> {turn}")
        reply = manager.handle_turn(turn)
        print(f"  bot: {' '.join(reply)}")
        _print_timing(manager)

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
        run_scenario("2. Event-anchored", ["a 15-minute chat a day or two after the Project Alpha Kick-off event"])
        run_scenario("3. Calendar-arithmetic", ["1-hour meeting for the last weekday of this month"])
        run_scenario(
            "4. Vague/multi-constraint",
            ["next week, not too early, not on Wednesday", "actually we need a full hour now"],
        )

        manager, captured = make_manager()
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
            "6. Dynamic buffer",
            [
                "evening, after 7, but I need an hour to decompress after my last meeting",
                "actually we need a full hour now",
            ],
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
