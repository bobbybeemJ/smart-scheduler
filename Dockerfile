FROM python:3.12-slim

WORKDIR /app

# espeak-ng: required by pyttsx3's Linux backend (the offline TTS fallback used when edge-tts,
# an unofficial API, fails). Render's native Python buildpack doesn't reliably allow apt
# installs, which is exactly why this project deploys via Dockerfile instead.
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY static/ ./static/

# Render sets $PORT at runtime; 8000 is only the local-dev fallback.
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
