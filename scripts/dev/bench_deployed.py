"""One-off: measures real client-observed latency against the live deployed service."""

import asyncio
import json
import sys
import time

import websockets

URL = "wss://smart-scheduler-693682345402.asia-south2.run.app/ws"


async def main(transcript: str):
    async with websockets.connect(URL) as ws:
        t0 = time.perf_counter()
        await ws.send(json.dumps({"type": "transcript", "text": transcript, "source": "web_speech"}))

        last_audio_time = None
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
            msg = json.loads(raw)
            now = time.perf_counter()
            elapsed_ms = (now - t0) * 1000
            if msg["type"] == "audio_clause" and msg["index"] == -1:
                print(f"filler audio arrived at t+{elapsed_ms:.0f}ms")
            elif msg["type"] == "reply_clauses":
                print(f"reply_clauses arrived at t+{elapsed_ms:.0f}ms: {msg['clauses']}")
            elif msg["type"] == "audio_clause" and msg["index"] >= 0:
                print(f"  real audio_clause {msg['index']} at t+{elapsed_ms:.0f}ms")
                last_audio_time = now

        # (loop exits via timeout after the last message)


if __name__ == "__main__":
    transcript = sys.argv[1] if len(sys.argv) > 1 else "1-hour meeting for the last weekday of this month"
    try:
        asyncio.run(main(transcript))
    except asyncio.TimeoutError:
        pass
