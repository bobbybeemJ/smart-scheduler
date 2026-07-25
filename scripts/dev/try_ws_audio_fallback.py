"""Phase 6 verification of the audio_chunk fallback path (what the browser sends when Web
Speech API is unavailable) through the real websocket - reuses the Phase 4 webm/opus sample."""

import asyncio
import base64
import json
import pathlib
import sys

import websockets

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
DEFAULT_SAMPLE = ROOT / "scratch_scheduling_sample.webm"


async def run(sample_path: pathlib.Path, url="ws://127.0.0.1:8001/ws"):
    if not sample_path.exists():
        raise SystemExit(f"{sample_path} not found - run scripts/dev/make_scheduling_webm_sample.py first")

    audio_b64 = base64.b64encode(sample_path.read_bytes()).decode("ascii")
    async with websockets.connect(url) as ws:
        print(f"> sending audio_chunk (fallback path, real webm/opus bytes from {sample_path.name})")
        await ws.send(json.dumps({"type": "audio_chunk", "data_base64": audio_b64, "is_final": True}))

        raw = await asyncio.wait_for(ws.recv(), timeout=30)
        msg = json.loads(raw)
        if msg["type"] == "audio_clause" and msg["index"] == -1:
            print(f"  filler audio_clause: {len(base64.b64decode(msg['audio_base64']))} bytes")
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = json.loads(raw)
        if msg["type"] == "error":
            print(f"  ERROR: {msg['message']}")
            return
        assert msg["type"] == "reply_clauses", f"expected reply_clauses, got {msg['type']}"
        clauses = msg["clauses"]
        print(f"  reply_clauses: {clauses}")

        for _ in range(len(clauses)):
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            msg = json.loads(raw)
            if msg["type"] == "error":
                print(f"  ERROR: {msg['message']}")
                break
            audio_bytes = base64.b64decode(msg["audio_base64"])
            print(f"  audio_clause {msg['index']}: {len(audio_bytes)} bytes, mime={msg['mime_type']}")


if __name__ == "__main__":
    path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_SAMPLE
    asyncio.run(run(path))
