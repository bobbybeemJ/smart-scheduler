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
  micBtn.disabled = false;
  micHint.textContent = value ? "Listening… tap again to stop" : "Tap the mic to start speaking";
  micBtn.classList.toggle("listening", value);
  micBtn.setAttribute("aria-label", value ? "Stop speaking" : "Start speaking");
}

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  // continuous=false made Chrome end the whole session on the FIRST brief pause mid-sentence,
  // silently truncating anything said after it (e.g. "schedule a meeting for next Wednesday"
  // <pause> "at 3pm" would only ever send the first half) - found from real usage where several
  // turns arrived as obvious sentence fragments. continuous=true keeps listening across pauses;
  // the session now only ends when the user deliberately taps the mic again to stop.
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.lang = "en-US";

  let accumulatedTranscript = "";

  recognition.onresult = (event) => {
    // In continuous mode a single session can still emit multiple separate final results (one
    // per natural pause) - only appending event.resultIndex onward (not resetting each time)
    // avoids re-processing results already accumulated from earlier in this same session.
    for (let i = event.resultIndex; i < event.results.length; i++) {
      if (event.results[i].isFinal) {
        accumulatedTranscript += event.results[i][0].transcript + " ";
      }
    }
  };
  recognition.onerror = (e) => {
    logEntry(`recognition error: ${e.error}`, "error");
    setListening(false); // otherwise an error left the button stuck showing "tap to stop"
  };
  recognition.onend = () => {
    setListening(false);
    const text = accumulatedTranscript.trim();
    accumulatedTranscript = "";
    if (text) sendTranscript(text, "web_speech");
  };
  // The recognition engine takes a real, variable moment after start() to actually begin
  // capturing audio (it's negotiating with a backend service, not just flipping a local flag) -
  // found from real usage: speaking within ~1-2s of tapping the mic was getting clipped or
  // missed entirely, because the UI said "Listening…" the instant the button was tapped (set
  // synchronously, right after calling start()), well before the engine had actually finished
  // initializing - so the UI was lying about being ready. onstart fires only once the engine is
  // truly capturing, so gating the "Listening" cue on it instead of on the tap means the user
  // only ever sees "Listening" once it's genuinely true - no arbitrary wait imposed, just an
  // honest signal instead of an optimistic one.
  recognition.onstart = () => {
    setListening(true);
  };

  micBtn.onclick = () => {
    if (listening) {
      recognition.stop();
    } else {
      accumulatedTranscript = "";
      micBtn.disabled = true; // briefly, until onstart confirms the engine is actually ready
      micHint.textContent = "Starting…";
      recognition.start();
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
