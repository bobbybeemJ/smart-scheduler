"""Manual verification: runs extract_intent() in mock mode (zero cost) against all 7 scenario
phrasings, then makes a couple of real LLM calls (cost-conscious - only 2, not all 7) to prove
the live path also works and that the duration-guessing bug found earlier is fixed.

Run mock-only:  python -m scripts.dev.try_llm
Run with live calls too: python -m scripts.dev.try_llm --live
"""

import sys

from dotenv import load_dotenv

load_dotenv()

from app import config  # noqa: E402
from app.llm.client import extract_intent  # noqa: E402

MOCK_PHRASES = [
    "45 minutes sometime before my flight Friday at 6 PM",
    "a 15-minute chat a day or two after the Project Alpha Kick-off event",
    "1-hour meeting for the last weekday of this month",
    "next week, not too early, not on Wednesday",
    "our usual sync-up",
    "evening, after 7, but I need an hour to decompress after my last meeting",
    "Tuesday at 2pm",
]


def run_mock():
    config.settings.use_mock_llm = True
    print("=== Mock mode (zero token cost) ===")
    for phrase in MOCK_PHRASES:
        intent = extract_intent(phrase)
        print(f"\n{phrase!r}\n  -> {intent!r}")


def run_live():
    config.settings.use_mock_llm = False
    print("\n=== Live Gemini calls (2 real calls) ===")

    phrase = "next week, not too early, not on Wednesday"
    intent = extract_intent(phrase)
    print(f"\n{phrase!r}\n  -> {intent!r}")
    assert intent.duration_minutes is None, (
        f"Expected duration_minutes=None (not stated), got {intent.duration_minutes} - "
        "the earlier hallucination bug may have regressed."
    )
    print("  OK: duration_minutes correctly left null instead of guessed")

    phrase = "book a 15 minute chat a day or two after the Project Alpha Kick-off event"
    intent = extract_intent(phrase)
    print(f"\n{phrase!r}\n  -> {intent!r}")


if __name__ == "__main__":
    run_mock()
    if "--live" in sys.argv:
        run_live()
