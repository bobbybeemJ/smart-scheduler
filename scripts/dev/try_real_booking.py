"""REAL integration test: exercises the full pipeline against the actual Google Calendar (real
freebusy check, real event creation), then deletes the test event it creates. Uses the mock LLM
to avoid burning API quota - only the Calendar API calls are real.

This creates and then deletes one real event on your calendar. Run: python -m scripts.dev.try_real_booking
"""

import datetime as dt

from dotenv import load_dotenv

load_dotenv()

from app import config  # noqa: E402

config.settings.use_mock_llm = True

from app.calendar_client import client as calendar_client  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402


def main():
    created_event = {}

    def capturing_insert_event(summary, start, end):
        result = calendar_client.insert_event(summary, start, end)
        created_event["value"] = result
        return result

    manager = DialogueManager(
        find_event=calendar_client.find_event_by_name,
        find_last_meeting=calendar_client.find_last_event_of_day,
        freebusy_fn=calendar_client.freebusy,
        insert_event_fn=capturing_insert_event,
    )

    print("> 'Tuesday at 2pm' (real freebusy check against your actual calendar)")
    reply = manager.handle_turn("Tuesday at 2pm")
    print(f"  bot: {' '.join(reply)}")
    assert manager.state.phase == "confirming", f"expected to be offered a slot, got phase={manager.state.phase}"
    offered = manager.state.top_candidate
    print(f"  offered slot: {offered.start} -> {offered.end}")

    print("\n> 'yes, book it' (REAL write to your calendar)")
    reply2 = manager.handle_turn("yes, book it")
    print(f"  bot: {' '.join(reply2)}")
    assert manager.state.phase == "booked"
    event = created_event["value"]
    event_id = event["id"]
    print(f"  created real event id={event_id}")

    print("\n> Verifying the event actually exists via a fresh API read...")
    found = calendar_client.find_event_by_name(
        "Meeting (scheduled by NxD Smart Scheduler)",
        time_min=offered.start - dt.timedelta(minutes=5),
        time_max=offered.end + dt.timedelta(minutes=5),
    )
    assert found is not None, "could not find the event we just created - real write may have failed"
    assert found["start"] == offered.start
    print(f"  OK: found it via a real read - {found['summary']} at {found['start']}")

    print("\n> Cleaning up: deleting the test event...")
    calendar_client.delete_event(event_id)
    found_after_delete = calendar_client.find_event_by_name(
        "Meeting (scheduled by NxD Smart Scheduler)",
        time_min=offered.start - dt.timedelta(minutes=5),
        time_max=offered.end + dt.timedelta(minutes=5),
    )
    assert found_after_delete is None, "event still found after delete - cleanup may have failed"
    print("  OK: event deleted and confirmed gone")


if __name__ == "__main__":
    main()
