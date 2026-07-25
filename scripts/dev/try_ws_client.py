"""Phase 6 server-side round-trip verification: a scripted websocket client that simulates what
the browser sends, so the full transcript -> dialogue -> resolve -> TTS -> audio_clause pipeline
can be verified without a real browser/mic. The actual mic/Web-Speech-API/playback path still
needs a real browser test - this only proves the server side.

Run against a running server: python -m scripts.dev.try_ws_client ["text1" "text2" ...]
"""

import asyncio
import base64
import json
import sys

import websockets

DEFAULT_TURNS = [
    "45 minutes sometime before my flight Friday at 6 PM",
    "actually we need a full hour now",
]


async def run(turns, url="ws://127.0.0.1:8001/ws"):
    async with websockets.connect(url) as ws:
        for text in turns:
            print(f"\n> sending transcript: {text!r}")
            await ws.send(json.dumps({"type": "transcript", "text": text, "source": "web_speech"}))

            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            msg = json.loads(raw)
            if msg["type"] == "error":
                print(f"  ERROR: {msg['message']}")
                continue

            assert msg["type"] == "reply_clauses", f"expected reply_clauses first, got {msg['type']}"
            clauses = msg["clauses"]
            print(f"  reply_clauses: {clauses}")

            for _ in range(len(clauses)):
                raw = await asyncio.wait_for(ws.recv(), timeout=15)
                msg = json.loads(raw)
                if msg["type"] == "error":
                    print(f"  ERROR: {msg['message']}")
                    break
                assert msg["type"] == "audio_clause", f"expected audio_clause, got {msg['type']}"
                audio_bytes = base64.b64decode(msg["audio_base64"])
                print(f"  audio_clause {msg['index']}: {len(audio_bytes)} bytes, mime={msg['mime_type']}")


if __name__ == "__main__":
    turns = sys.argv[1:] or DEFAULT_TURNS
    asyncio.run(run(turns))
