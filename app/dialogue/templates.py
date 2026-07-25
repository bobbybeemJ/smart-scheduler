"""Reply assembly - plain Python string templates, never an LLM call. Each function returns a
list of clauses rather than one string: Phase 5 streams TTS synthesis clause-by-clause so audio
starts on the first clause instead of waiting for the whole reply (see the plan's architecture
decision on "streaming reinterpreted"). Only the factual parts (times/dates) ever appear here,
assembled from deterministic Python values - never from LLM-generated text."""

from __future__ import annotations

import datetime as dt

from app.schemas import ResolvedConstraints


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


def present_search_window(constraints: ResolvedConstraints) -> list[str]:
    """Placeholder until Phase 7 wires up real freebusy-based slot finding - acknowledges the
    search window rather than claiming to have found (or booked) an actual free slot."""
    window = constraints.search_windows[0]
    return [
        f"Okay, I'll look for a {constraints.duration_minutes}-minute slot",
        f"between {_format_dt(window.start)} and {_format_dt(window.end)}.",
        "(Real availability search comes next.)",
    ]


def duration_updated(new_duration: int) -> list[str]:
    return [f"Got it, updating this to {new_duration} minutes", "and keeping the day/time preference you already gave me."]
