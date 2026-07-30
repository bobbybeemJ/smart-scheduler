"""Thin wrappers around the real Google Calendar API - no mocking in this module (mocking
happens at the test layer via injected lookup functions/fixtures, never here).

Timezone handling: every datetime elsewhere in this app is naive "wall clock" time by design
(see app/dateresolve - keeps date arithmetic simple and deterministic). The real Google Calendar
API requires timezone-aware RFC3339 timestamps, and always returns timezone-aware ones back -
found via a real API call that failed with "400 Bad Request" on a naive timeMin/timeMax. This
module is the one place that boundary gets crossed: _localize() attaches settings.user_timezone
before every outbound call, and _to_naive_local() strips it back off every inbound response, so
naive datetimes are all the rest of the codebase ever has to deal with."""

from __future__ import annotations

import datetime as dt
import time
from typing import Optional
from zoneinfo import ZoneInfo

from dateutil import parser as date_parser
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from app.calendar_client.auth import build_credentials_from_env
from app.config import settings

_service: Optional[Resource] = None


def _is_transient_http_error(exc: BaseException) -> bool:
    return isinstance(exc, HttpError) and exc.resp is not None and exc.resp.status in (429, 500, 502, 503, 504)


# Applied only to reads and delete - both idempotent/safe to retry. NOT applied to insert_event:
# if a write's response is lost after the server already processed it, blindly retrying would
# risk creating a duplicate calendar event, which is worse than surfacing the failure and letting
# the user explicitly ask again (already handled by manager.py's graceful-degradation path).
_retry_transient = retry(
    retry=retry_if_exception(_is_transient_http_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=4),
    reraise=True,
)


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.user_timezone)


def _localize(value: dt.datetime) -> dt.datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=_tz())


def _to_naive_local(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(_tz()).replace(tzinfo=None)


def get_calendar_service() -> Resource:
    """Singleton, built once per process and reused across every call - avoids repeating the
    TLS/auth handshake on every turn (see the plan's latency optimization backlog)."""
    global _service
    if _service is None:
        creds = build_credentials_from_env()
        _service = build("calendar", "v3", credentials=creds)
    return _service


def warmup() -> None:
    """Pre-resolve DNS and warm the TCP/TLS connection to Google's API host in a background
    thread at server startup, so the first real freebusy/insert call of the process doesn't also
    pay for that handshake - see the plan's latency optimization backlog. Single-user app, so
    there's no per-request budget being spent here; this only ever runs once, at boot."""
    import threading
    import urllib.request

    def _do() -> None:
        try:
            urllib.request.urlopen("https://www.googleapis.com/", timeout=5)
        except Exception:
            pass  # any response at all (even 404/403) means DNS+TLS already succeeded

    threading.Thread(target=_do, daemon=True).start()


# Process-level cache, not per-session: this app deliberately has exactly one calendar (one
# hardcoded refresh token, no multi-tenant login - see the plan's scope decision), so there is
# only ever one user's data to cache and no cross-user leakage risk. Keyed on the exact queried
# window, since callers (slot_finder) already collapse a search into one freebusy call per
# window rather than per-candidate - re-querying the identical window happens across turns
# (duration change, check-then-book) but the window bounds are the same both times.
_freebusy_cache: dict[tuple[str, str, str], tuple[list[dict], float]] = {}
FREEBUSY_CACHE_TTL_SECONDS = 300  # matches the tested value from a comparable reference implementation


@_retry_transient
def list_calendars() -> list[dict]:
    service = get_calendar_service()
    return service.calendarList().list().execute().get("items", [])


@_retry_transient
def find_event_by_name(
    name: str,
    time_min: Optional[dt.datetime] = None,
    time_max: Optional[dt.datetime] = None,
    calendar_id: str = "primary",
) -> Optional[dict]:
    service = get_calendar_service()
    params = {"calendarId": calendar_id, "q": name, "singleEvents": True, "orderBy": "startTime"}
    if time_min is not None:
        params["timeMin"] = _localize(time_min).isoformat()
    if time_max is not None:
        params["timeMax"] = _localize(time_max).isoformat()

    events = service.events().list(**params).execute().get("items", [])
    if not events:
        return None
    return _to_simple_event(events[0])


@_retry_transient
def find_last_event_of_day(day: dt.date, calendar_id: str = "primary") -> Optional[dict]:
    service = get_calendar_service()
    day_start = _localize(dt.datetime.combine(day, dt.time.min)).isoformat()
    day_end = _localize(dt.datetime.combine(day, dt.time.max)).isoformat()

    events = (
        service.events()
        .list(calendarId=calendar_id, timeMin=day_start, timeMax=day_end, singleEvents=True, orderBy="startTime")
        .execute()
        .get("items", [])
    )
    if not events:
        return None
    return _to_simple_event(events[-1])


@_retry_transient
def freebusy(start: dt.datetime, end: dt.datetime, calendar_id: str = "primary") -> list[dict]:
    key = (_localize(start).isoformat(), _localize(end).isoformat(), calendar_id)
    cached = _freebusy_cache.get(key)
    if cached is not None:
        busy_periods, expires_at = cached
        if time.monotonic() < expires_at:
            return busy_periods

    service = get_calendar_service()
    body = {
        "timeMin": key[0],
        "timeMax": key[1],
        "items": [{"id": calendar_id}],
    }
    result = service.freebusy().query(body=body).execute()
    busy_raw = result["calendars"][calendar_id]["busy"]
    busy_periods = [
        {"start": _to_naive_local(date_parser.parse(b["start"])), "end": _to_naive_local(date_parser.parse(b["end"]))}
        for b in busy_raw
    ]
    _freebusy_cache[key] = (busy_periods, time.monotonic() + FREEBUSY_CACHE_TTL_SECONDS)
    return busy_periods


def insert_event(summary: str, start: dt.datetime, end: dt.datetime, calendar_id: str = "primary") -> dict:
    """Deliberately NOT retried automatically - see the module-level note on _retry_transient.
    A transient failure here surfaces immediately via manager.py's graceful-degradation path,
    letting the user explicitly ask again rather than risking a duplicate booking."""
    service = get_calendar_service()
    body = {
        "summary": summary,
        "start": {"dateTime": _localize(start).isoformat(), "timeZone": settings.user_timezone},
        "end": {"dateTime": _localize(end).isoformat(), "timeZone": settings.user_timezone},
    }
    result = service.events().insert(calendarId=calendar_id, body=body).execute()
    # A just-made booking can make any cached freebusy window stale (the same conversation might
    # immediately search/check again nearby) - clearing entirely is simpler and safer than trying
    # to figure out which cached windows overlap the new event, and bookings are rare relative to
    # searches so the lost cache hits don't cost anything real.
    _freebusy_cache.clear()
    return result


@_retry_transient
def delete_event(event_id: str, calendar_id: str = "primary") -> None:
    service = get_calendar_service()
    service.events().delete(calendarId=calendar_id, eventId=event_id).execute()


@_retry_transient
def get_event(event_id: str, calendar_id: str = "primary") -> dict:
    """Unambiguous lookup by the exact id insert_event() returned - unlike find_event_by_name,
    this can never accidentally match a different same-named event nearby (found the hard way:
    a leftover orphaned test event confused a name+time-range search into verifying the wrong
    event entirely)."""
    service = get_calendar_service()
    event = service.events().get(calendarId=calendar_id, eventId=event_id).execute()
    return _to_simple_event(event)


def _to_simple_event(event: dict) -> dict:
    return {
        "id": event["id"],
        "summary": event.get("summary"),
        "start": _parse_event_datetime(event["start"]),
        "end": _parse_event_datetime(event["end"]),
    }


def _parse_event_datetime(value: dict) -> dt.datetime:
    raw = value.get("dateTime") or value.get("date")
    return _to_naive_local(date_parser.parse(raw))
