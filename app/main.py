"""FastAPI app: static frontend, websocket route, health check, and a boot-time TTS engine
health check (visibility into edge-tts's unofficial-API risk, per the plan)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.tts.streamer import check_engine_health, synthesize_filler
from app.ws.handler import websocket_endpoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Running boot-time TTS engine health check...")
    health = await check_engine_health()
    logger.info("TTS engine health: %s", health)
    if not health["edge_tts"] and not health["pyttsx3"]:
        logger.error("Both TTS engines failed the health check - voice replies will not work.")

    # Pre-warm the filler-phrase cache so the very first real turn doesn't pay a fresh
    # synthesis cost for it either - see app/tts/streamer.py's perceived-latency mask.
    try:
        await synthesize_filler()
        logger.info("Filler phrase pre-warmed.")
    except Exception:
        logger.exception("Failed to pre-warm filler phrase - it will synthesize fresh on first use")

    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/")
def index():
    # Cross-Origin-Opener-Policy + Cross-Origin-Embedder-Policy (credentialless) make this page
    # cross-origin isolated, which is what grants it SharedArrayBuffer - onnxruntime-web's WASM
    # binary needs that to load at all (independent of the numThreads=1 pin in index.html, which
    # only avoids the separate WebGPU/JSEP backend). "credentialless" (not "require-corp") lets
    # the vad-web/onnxruntime-web CDN scripts load without needing their own CORP headers.
    response = FileResponse("static/index.html")
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Embedder-Policy"] = "credentialless"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_api_websocket_route("/ws", websocket_endpoint)
