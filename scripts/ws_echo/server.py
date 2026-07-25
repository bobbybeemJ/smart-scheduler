"""Phase 0 sanity check: bare mic -> websocket -> echo round trip, no LLM/calendar involved.
Run with: python -m uvicorn scripts.ws_echo.server:app --reload --port 8000
Then open http://localhost:8000 in Chrome and speak into the mic."""

import pathlib

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

app = FastAPI()

INDEX_HTML = (pathlib.Path(__file__).resolve().parent / "index.html").read_text()


@app.get("/", response_class=HTMLResponse)
def index():
    return INDEX_HTML


@app.websocket("/ws")
async def echo(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            text = await websocket.receive_text()
            await websocket.send_text(f"echo: {text}")
    except WebSocketDisconnect:
        pass
