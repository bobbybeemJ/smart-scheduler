const statusEl = document.getElementById("status");
const micBtn = document.getElementById("micBtn");
const logEl = document.getElementById("log");

function logEntry(text, cls) {
  const div = document.createElement("div");
  div.className = `log-entry ${cls}`;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

// --- Websocket ---
const ws = new WebSocket(`ws://${location.host}/ws`);
ws.onopen = () => {
  statusEl.textContent = "connected";
  micBtn.disabled = false;
};
ws.onerror = () => {
  statusEl.textContent = "websocket error";
};
ws.onclose = () => {
  statusEl.textContent = "disconnected";
  micBtn.disabled = true;
};
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "reply_clauses") {
    logEntry(msg.clauses.join(" "), "bot");
  } else if (msg.type === "audio_clause") {
    enqueueAudio(msg.audio_base64, msg.mime_type);
  } else if (msg.type === "error") {
    logEntry(msg.message, "error");
  }
};

// --- Sequential audio playback queue ---
// New clauses can arrive while an earlier one is still playing; each is queued and played
// in order, but clause 1's audio starts the moment it arrives rather than waiting for the rest.
const audioQueue = [];
let isPlaying = false;

function enqueueAudio(base64, mimeType) {
  audioQueue.push(`data:${mimeType};base64,${base64}`);
  if (!isPlaying) playNext();
}

function playNext() {
  const src = audioQueue.shift();
  if (!src) {
    isPlaying = false;
    return;
  }
  isPlaying = true;
  const audio = new Audio(src);
  audio.onended = playNext;
  audio.onerror = playNext;
  audio.play().catch(() => playNext());
}

// --- Speech input: Web Speech API primary, MediaRecorder fallback ---
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let mediaRecorder = null;
let recordedChunks = [];
let listening = false;

function sendTranscript(text, source) {
  logEntry(text, "user");
  ws.send(JSON.stringify({ type: "transcript", text, source }));
}

function setListening(value) {
  listening = value;
  micBtn.textContent = value ? "Listening... (click to stop)" : "Start speaking";
  micBtn.classList.toggle("listening", value);
}

if (SpeechRecognition) {
  statusEl.textContent = "connected (Web Speech API)";
  recognition = new SpeechRecognition();
  recognition.continuous = false;
  recognition.interimResults = false;
  recognition.lang = "en-US";

  recognition.onresult = (event) => {
    const text = event.results[event.results.length - 1][0].transcript;
    sendTranscript(text, "web_speech");
  };
  recognition.onerror = (e) => logEntry(`recognition error: ${e.error}`, "error");
  recognition.onend = () => setListening(false);

  micBtn.onclick = () => {
    if (listening) {
      recognition.stop();
    } else {
      recognition.start();
      setListening(true);
    }
  };
} else {
  statusEl.textContent = "connected (Web Speech API unavailable - using recorder fallback)";

  micBtn.onclick = async () => {
    if (!listening) {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorder.ondataavailable = (e) => recordedChunks.push(e.data);
      mediaRecorder.onstop = async () => {
        const blob = new Blob(recordedChunks, { type: "audio/webm" });
        const base64 = await blobToBase64(blob);
        ws.send(JSON.stringify({ type: "audio_chunk", data_base64: base64, is_final: true }));
        stream.getTracks().forEach((track) => track.stop());
      };
      mediaRecorder.start();
      setListening(true);
    } else {
      mediaRecorder.stop();
      setListening(false);
    }
  };
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onloadend = () => resolve(reader.result.split(",")[1]);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}
