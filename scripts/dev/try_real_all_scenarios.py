"""Real-calendar verification for all 6 assignment scenarios (requested after a fair challenge:
mocks only prove our code handles a shape of data we made up, not that it handles the real API).

Seeds 2 real events (Project Alpha Kick-off, an evening meeting for the dynamic-buffer case),
then runs each of the 6 scenarios through the real DialogueManager (real freebusy, real
find_event/find_last_meeting, real insert_event), books whatever slot is offered, independently
re-reads it to confirm, then deletes it. Cleans up the 2 seed events at the end too. Uses the
mock LLM (zero Gemini cost) since this is specifically about the Calendar API integration, not
re-testing extraction (already covered separately).

Run: python -m scripts.dev.try_real_all_scenarios
"""

import datetime as dt

from dotenv import load_dotenv

load_dotenv()

from app import config  # noqa: E402

config.settings.use_mock_llm = True

from app.calendar_client import client as calendar_client  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402

created_ids: list[str] = []


def make_manager() -> DialogueManager:
    return DialogueManager(
        find_event=calendar_client.find_event_by_name,
        find_last_meeting=calendar_client.find_last_event_of_day,
        freebusy_fn=calendar_client.freebusy,
        insert_event_fn=calendar_client.insert_event,
    )


def run_scenario(name: str, turns: list[str]) -> None:
    print(f"\n{'=' * 70}\n{name}\n{'=' * 70}")
    manager = make_manager()
    reply = []
    for turn in turns:
        print(f"> {turn}")
        reply = manager.handle_turn(turn)
        print(f"  bot: {' '.join(reply)}")

    if manager.state.phase != "confirming" or manager.state.top_candidate is None:
        print(f"  RESULT: no bookable slot offered (phase={manager.state.phase}) - real freebusy was still checked.")
        return

    offered = manager.state.top_candidate
    reply = manager.handle_turn("yes, book it")
    print(f"> yes, book it\n  bot: {' '.join(reply)}")

    if manager.state.phase != "booked":
        print("  RESULT: booking did not complete.")
        return

    found = calendar_client.find_event_by_name(
        "Meeting (scheduled by NxD Smart Scheduler)",
        time_min=offered.start - dt.timedelta(minutes=5),
        time_max=offered.end + dt.timedelta(minutes=5),
    )
    assert found is not None, "booked event not found via independent real read"
    assert found["start"] == offered.start
    created_ids.append(found["id"])
    print(f"  RESULT: OK - booked and independently re-read at {found['start']}, event id={found['id']}")

    calendar_client.delete_event(found["id"])
    created_ids.remove(found["id"])
    still_there = calendar_client.find_event_by_name(
        "Meeting (scheduled by NxD Smart Scheduler)",
        time_min=offered.start - dt.timedelta(minutes=5),
        time_max=offered.end + dt.timedelta(minutes=5),
    )
    assert still_there is None, "event still found after delete"
    print("  Cleaned up: event deleted and confirmed gone.")


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

        manager = make_manager()
        manager.state.usual_meeting_defaults["usual sync-up"] = 30
        print(f"\n{'=' * 70}\n5. Contextual memory\n{'=' * 70}")
        print("> next week, not too early, not on Wednesday")
        print(f"  bot: {' '.join(manager.handle_turn('next week, not too early, not on Wednesday'))}")
        print("> our usual sync-up")
        reply = manager.handle_turn("our usual sync-up")
        print(f"  bot: {' '.join(reply)}")
        if manager.state.phase == "confirming" and manager.state.top_candidate is not None:
            offered = manager.state.top_candidate
            reply = manager.handle_turn("yes, book it")
            print(f"> yes, book it\n  bot: {' '.join(reply)}")
            if manager.state.phase == "booked":
                found = calendar_client.find_event_by_name(
                    "Meeting (scheduled by NxD Smart Scheduler)",
                    time_min=offered.start - dt.timedelta(minutes=5),
                    time_max=offered.end + dt.timedelta(minutes=5),
                )
                assert found is not None and found["start"] == offered.start
                print(f"  RESULT: OK - booked and independently re-read at {found['start']}, event id={found['id']}")
                calendar_client.delete_event(found["id"])
                print("  Cleaned up: event deleted.")
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
        for event_id in created_ids:
            print(f"  Deleting leftover event {event_id} (a scenario above didn't clean up its own)")
            calendar_client.delete_event(event_id)
        calendar_client.delete_event(kickoff_event["id"])
        calendar_client.delete_event(evening_event["id"])
        print("  Seed events deleted.")


if __name__ == "__main__":
    main()
