"""Thin wrappers around the real Google Calendar API - no mocking in this module (mocking
happens at the test layer via injected lookup functions/fixtures, never here)."""

from __future__ import annotations

import datetime as dt
from typing import Optional

from dateutil import parser as date_parser
from googleapiclient.discovery import Resource, build

from app.calendar_client.auth import build_credentials_from_env

_service: Optional[Resource] = None


def get_calendar_service() -> Resource:
    """Singleton, built once per process and reused across every call - avoids repeating the
    TLS/auth handshake on every turn (see the plan's latency optimization backlog)."""
    global _service
    if _service is None:
        creds = build_credentials_from_env()
        _service = build("calendar", "v3", credentials=creds)
    return _service


def list_calendars() -> list[dict]:
    service = get_calendar_service()
    return service.calendarList().list().execute().get("items", [])


def find_event_by_name(
    name: str,
    time_min: Optional[dt.datetime] = None,
    time_max: Optional[dt.datetime] = None,
    calendar_id: str = "primary",
) -> Optional[dict]:
    service = get_calendar_service()
    params = {"calendarId": calendar_id, "q": name, "singleEvents": True, "orderBy": "startTime"}
    if time_min is not None:
        params["timeMin"] = time_min.isoformat()
    if time_max is not None:
        params["timeMax"] = time_max.isoformat()

    events = service.events().list(**params).execute().get("items", [])
    if not events:
        return None
    return _to_simple_event(events[0])


def find_last_event_of_day(day: dt.date, calendar_id: str = "primary") -> Optional[dict]:
    service = get_calendar_service()
    day_start = dt.datetime.combine(day, dt.time.min).isoformat()
    day_end = dt.datetime.combine(day, dt.time.max).isoformat()

    events = (
        service.events()
        .list(calendarId=calendar_id, timeMin=day_start, timeMax=day_end, singleEvents=True, orderBy="startTime")
        .execute()
        .get("items", [])
    )
    if not events:
        return None
    return _to_simple_event(events[-1])


def freebusy(start: dt.datetime, end: dt.datetime, calendar_id: str = "primary") -> list[dict]:
    service = get_calendar_service()
    body = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "items": [{"id": calendar_id}]}
    result = service.freebusy().query(body=body).execute()
    busy_periods = result["calendars"][calendar_id]["busy"]
    return [{"start": date_parser.parse(b["start"]), "end": date_parser.parse(b["end"])} for b in busy_periods]


def insert_event(summary: str, start: dt.datetime, end: dt.datetime, calendar_id: str = "primary") -> dict:
    service = get_calendar_service()
    body = {
        "summary": summary,
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    return service.events().insert(calendarId=calendar_id, body=body).execute()


def _to_simple_event(event: dict) -> dict:
    return {
        "id": event["id"],
        "summary": event.get("summary"),
        "start": _parse_event_datetime(event["start"]),
        "end": _parse_event_datetime(event["end"]),
    }


def _parse_event_datetime(value: dict) -> dt.datetime:
    raw = value.get("dateTime") or value.get("date")
    return date_parser.parse(raw)
