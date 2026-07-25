"""Scores candidate slots by closeness to the user's stated preference, instead of just
returning the first chronologically available slot. Lower score = better match; callers sort
ascending."""

from __future__ import annotations

import datetime as dt

from app.schemas import ResolvedConstraints, TimePreference, TimeWindow

_EPOCH = dt.datetime(2000, 1, 1)  # arbitrary fixed reference, avoids any local-timezone quirk


def _seconds_since_epoch(value: dt.datetime) -> float:
    return (value - _EPOCH).total_seconds()


def score_candidate(candidate: TimeWindow, constraints: ResolvedConstraints) -> float:
    if constraints.time_preference == TimePreference.NOT_TOO_EARLY:
        return -_seconds_since_epoch(candidate.start)  # later is better
    if constraints.time_preference == TimePreference.NOT_TOO_LATE:
        return _seconds_since_epoch(candidate.start)  # earlier is better
    if constraints.earliest_start is not None:
        # dynamic_buffer case: closest to the computed earliest-possible start wins
        return abs(_seconds_since_epoch(candidate.start) - _seconds_since_epoch(constraints.earliest_start))
    # No explicit preference signal: soonest available is the sensible default.
    return _seconds_since_epoch(candidate.start)


def rank_candidates(candidates: list[TimeWindow], constraints: ResolvedConstraints) -> list[TimeWindow]:
    return sorted(candidates, key=lambda c: score_candidate(c, constraints))
