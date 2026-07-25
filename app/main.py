"""FastAPI app: static frontend, websocket route, health check, and a boot-time TTS engine
health check (visibility into edge-tts's unofficial-API risk, per the plan)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.tts.streamer import check_engine_health
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
    yield


app = FastAPI(lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "healthy"}


@app.get("/")
def index():
    return FileResponse("static/index.html")


app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_api_websocket_route("/ws", websocket_endpoint)
