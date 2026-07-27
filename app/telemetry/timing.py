"""Per-turn latency instrumentation - one structured log line per conversational turn covering
every stage (STT, LLM extraction, deterministic resolve, real Calendar API calls, TTS), so the
actual end-to-end pipeline latency is visible in server logs for a real running conversation.

Note on resolve_ms vs calendar_ms: resolve() is "deterministic Python" by architecture, but for
the event_relative and dynamic_buffer cases it also invokes the injected find_event/
find_last_meeting callbacks, which are real Calendar API calls in production. Those are counted
inside resolve_ms here, not calendar_ms - separating them would need timing hooks threaded into
the resolver's callback wrappers themselves. Documented here rather than silently blurring the
distinction."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("nxd.telemetry")


@dataclass
class TurnTiming:
    stt_ms: float = 0.0
    llm_ms: float = 0.0
    resolve_ms: float = 0.0
    calendar_ms: float = 0.0
    tts_first_clause_ms: Optional[float] = None
    tts_total_ms: Optional[float] = None
    total_ms: Optional[float] = None

    def log(self, transcript: str) -> None:
        logger.info(
            "turn timing | transcript=%r | stt=%.0fms llm=%.0fms resolve=%.0fms calendar=%.0fms "
            "tts_first=%s tts_total=%s | total=%s",
            transcript,
            self.stt_ms,
            self.llm_ms,
            self.resolve_ms,
            self.calendar_ms,
            _fmt(self.tts_first_clause_ms),
            _fmt(self.tts_total_ms),
            _fmt(self.total_ms),
        )


def _fmt(value: Optional[float]) -> str:
    return f"{value:.0f}ms" if value is not None else "n/a"


class Stopwatch:
    """`with Stopwatch() as sw: ...` then read sw.elapsed_ms."""

    def __enter__(self) -> "Stopwatch":
        self._start = time.perf_counter()
        self.elapsed_ms = 0.0
        return self

    def __exit__(self, *exc) -> bool:
        self.elapsed_ms = (time.perf_counter() - self._start) * 1000
        return False
