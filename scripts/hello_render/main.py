"""Throwaway hello-world app used only to prove the Render + Dockerfile deploy path works
before any real feature code depends on it. Not imported by, or coupled to, the real app."""

import os

from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {"status": "ok", "message": "NxD Smart Scheduler - Phase 0 Render deploy check"}


@app.get("/health")
def health():
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
