"""Phase 0 sanity check: one real Gemini call. Costs one real token-metered request -
run sparingly, not in a loop. Requires GEMINI_API_KEY in .env."""

import os
import time

from dotenv import load_dotenv
from google import genai

load_dotenv()


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set in .env")

    model = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite")
    client = genai.Client(api_key=api_key)

    prompt = "Reply with exactly the two words: pong received"

    start = time.perf_counter()
    response = client.models.generate_content(model=model, contents=prompt)
    elapsed_ms = (time.perf_counter() - start) * 1000

    print(f"model: {model}")
    print(f"round-trip latency: {elapsed_ms:.0f} ms")
    print(f"response text: {response.text!r}")


if __name__ == "__main__":
    main()
