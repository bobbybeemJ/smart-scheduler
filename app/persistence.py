"""Simple JSON-file-backed persistence for the "usual meeting" defaults, so they can survive
across websocket reconnects and (as long as the file survives) server restarts - previously this
only lived in memory for the lifetime of a single websocket connection, which was a real gap
flagged during review. Deliberately not a database: this is a single-user personal assistant, and
a JSON file is the simplest thing that satisfies the project's 100%-free, no-extra-infra
constraint. Known limitation: the deployed container's filesystem is ephemeral, so a full
restart/redeploy still loses this - same documented tradeoff as the OAuth token situation
elsewhere in this project.

Opt-in, not automatic: DialogueManager only touches this when explicitly constructed with
persist_usual_meeting_defaults=True (the real app does; existing tests don't, so nothing about
this changes any existing test's behavior)."""

from __future__ import annotations

import json
import pathlib
from typing import Optional

_DEFAULT_STORE_PATH = pathlib.Path(__file__).resolve().parent.parent / "data" / "usual_meeting_defaults.json"


def load_usual_meeting_defaults(path: Optional[pathlib.Path] = None) -> dict[str, int]:
    path = path or _DEFAULT_STORE_PATH
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_usual_meeting_defaults(defaults: dict[str, int], path: Optional[pathlib.Path] = None) -> None:
    path = path or _DEFAULT_STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(defaults))
