"""One-off diagnostic: list models available to this API key so we can pick a valid
current free-tier model id instead of guessing (model names/availability drift over time)."""

import os

from dotenv import load_dotenv
from google import genai

load_dotenv()


def main():
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit("GEMINI_API_KEY is not set in .env")

    client = genai.Client(api_key=api_key)
    for model in client.models.list():
        name = getattr(model, "name", None)
        methods = getattr(model, "supported_actions", None) or getattr(
            model, "supported_generation_methods", None
        )
        print(name, "->", methods)


if __name__ == "__main__":
    main()
