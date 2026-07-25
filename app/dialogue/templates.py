"""Reply assembly - plain Python string templates, never an LLM call. Each function returns a
list of clauses rather than one string: Phase 5 streams TTS synthesis clause-by-clause so audio
starts on the first clause instead of waiting for the whole reply (see the plan's architecture
decision on "streaming reinterpreted"). Only the factual parts (times/dates) ever appear here,
assembled from deterministic Python values - never from LLM-generated text."""

from __future__ import annotations

import datetime as dt

from app.schemas import ResolvedConstraints, TimeWindow


def _format_dt(value: dt.datetime) -> str:
    return value.strftime("%A, %B %d at %I:%M %p").replace(" 0", " ")


def ask_duration() -> list[str]:
    return ["Sure.", "How long should this meeting be?"]


def ask_day_time_preference() -> list[str]:
    return ["Got it.", "What day or time works for you?"]


def ask_duration_for_untethered_update() -> list[str]:
    return ["I don't have a meeting in progress to update yet.", "What would you like to schedule?"]


def could_not_find_reference(detail: str) -> list[str]:
    return [f"I couldn't find that - {detail}.", "Could you tell me more, or pick a different reference?"]


def llm_failure() -> list[str]:
    return ["Sorry, I had trouble understanding that.", "Could you say that again?"]


def duration_updated(new_duration: int) -> list[str]:
    return [f"Got it, updating this to {new_duration} minutes", "and keeping the day/time preference you already gave me."]


def present_available_slot(slots: list[TimeWindow], duration_minutes: int, was_widened: bool) -> list[str]:
    """The real answer, replacing Phase 3's placeholder - actual free slot(s) found via
    freebusy, ranked by closeness to the stated preference. Presents up to a few ranked options
    instead of just "the next open slot" - still exactly 3 clauses regardless of how many
    options there are (1-3), so this doesn't add extra TTS synthesis calls."""
    if was_widened:
        lead_in = (
            "I couldn't find anything in your original window, but I found this a bit further out:"
            if len(slots) == 1
            else "I couldn't find anything in your original window, but here are a few options a bit further out:"
        )
    else:
        lead_in = "I found a slot that works:" if len(slots) == 1 else "I found a few options:"

    if len(slots) == 1:
        body = f"{_format_dt(slots[0].start)}, for {duration_minutes} minutes."
        ask = "Should I book it?"
    else:
        options = "; ".join(_format_dt(slot.start) for slot in slots)
        body = f"{options}; all for {duration_minutes} minutes."
        ask = "Which one works, or I can book the first one?"

    return [lead_in, body, ask]


def no_slots_even_after_widening() -> list[str]:
    return [
        "I couldn't find anything free, even after widening the search.",
        "Would you like to try a different duration, or a different day range?",
    ]


def booking_confirmed(slot: TimeWindow) -> list[str]:
    return [f"Done - booked for {_format_dt(slot.start)}.", "Anything else?"]


def nothing_pending_to_confirm() -> list[str]:
    return ["I don't have a slot pending to confirm.", "What would you like to schedule?"]


def calendar_failure() -> list[str]:
    """Graceful degradation - the Calendar API is unavailable/erroring. Ask a clarifying
    question instead of crashing or claiming a slot exists that was never actually checked."""
    return [
        "I'm having trouble reaching the calendar right now.",
        "Could you try again in a moment?",
    ]
