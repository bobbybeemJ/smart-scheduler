"""Phase 3 manual verification: a plain stdin/stdout loop against DialogueManager, USE_MOCK_LLM
by default, for fast iteration without voice. Type 'exit' to quit.

Run: python -m scripts.terminal_chat
"""

import datetime as dt
import sys

from dotenv import load_dotenv

load_dotenv()

from app import config  # noqa: E402
from app.dialogue.manager import DialogueManager  # noqa: E402


def main():
    config.settings.use_mock_llm = True
    now = dt.datetime(2026, 7, 22, 9, 0)  # fixed "now" for reproducible manual testing
    manager = DialogueManager(now_fn=lambda: now)

    print(f"NxD Smart Scheduler - terminal chat (mock LLM, now={now}). Type 'exit' to quit.\n")
    for line in sys.stdin:
        transcript = line.strip()
        if not transcript:
            continue
        if transcript.lower() in {"exit", "quit"}:
            break
        print(f"> {transcript}")
        reply_clauses = manager.handle_turn(transcript)
        print(f"bot: {' '.join(reply_clauses)}\n")


if __name__ == "__main__":
    main()
