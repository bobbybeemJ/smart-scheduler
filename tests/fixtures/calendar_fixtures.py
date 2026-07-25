"""Canned Calendar-API-shaped fixtures. Lets the resolver's injected calendar-lookup callbacks
be exercised without hitting the network. Shared by the Phase 1 manual verification script and
the Phase 9 pytest suite."""

from __future__ import annotations

import datetime as dt
from typing import Callable, Optional


def make_event(summary: str, start: dt.datetime, end: dt.datetime) -> dict:
    return {"summary": summary, "start": start, "end": end}


def make_find_event(events_by_name: dict[str, dict]) -> Callable[[str], Optional[dict]]:
    def _find_event(name: str) -> Optional[dict]:
        return events_by_name.get(name)

    return _find_event


def make_find_last_meeting(events_by_date: dict[dt.date, dict]) -> Callable[[dt.date], Optional[dict]]:
    def _find_last_meeting(day: dt.date) -> Optional[dict]:
        return events_by_date.get(day)

    return _find_last_meeting


PROJECT_ALPHA_KICKOFF = make_event(
    "Project Alpha Kick-off",
    dt.datetime(2026, 7, 20, 10, 0),
    dt.datetime(2026, 7, 20, 11, 0),
)
