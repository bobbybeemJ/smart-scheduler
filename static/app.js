const statusEl = document.getElementById("status");
const statusPill = document.getElementById("statusPill");
const micBtn = document.getElementById("micBtn");
const micHint = document.getElementById("micHint");
const logEl = document.getElementById("log");
const emptyState = document.getElementById("emptyState");

function setStatus(text, state) {
  statusEl.textContent = text;
  statusPill.dataset.state = state;
}

function logEntry(text, cls) {
  if (emptyState) emptyState.remove();
  const div = document.createElement("div");
  div.className = `log-entry ${cls}`;
  div.textContent = text;
  logEl.appendChild(div);
  logEl.scrollTop = logEl.scrollHeight;
}

// --- Websocket ---
const usingFallback = !(window.SpeechRecognition || window.webkitSpeechRecognition);
const wsProtocol = location.protocol === "https:" ? "wss:" : "ws:";
const ws = new WebSocket(`${wsProtocol}//${location.host}/ws`);
ws.onopen = () => {
  setStatus("Connected", "connected");
  micBtn.disabled = false;
  if (!usingFallback) micHint.textContent = "Tap the mic to start speaking";
};
ws.onerror = () => {
  setStatus("Connection error", "error");
};
ws.onclose = () => {
  setStatus("Disconnected", "disconnected");
  micBtn.disabled = true;
  micHint.textContent = "Reconnect by reloading the page";
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
  micHint.textContent = value ? "Listening… tap again to stop" : "Tap the mic to start speaking";
  micBtn.classList.toggle("listening", value);
  micBtn.setAttribute("aria-label", value ? "Stop speaking" : "Start speaking");
}

if (SpeechRecognition) {
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
  micHint.textContent = "Web Speech API unavailable — using recorder fallback";

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
