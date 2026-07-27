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
// Tracks whether the TRUE final clause of the CURRENT turn's reply has actually been queued -
// "the audioQueue is currently empty" is NOT the same thing and was the bug: the filler
// (index=-1) is sent and often finishes playing well before the real reply even exists yet
// (it's synthesized before the LLM/resolve/calendar work even starts), so the queue goes empty
// mid-turn for a reason that has nothing to do with the bot being done - that momentary
// emptiness was wrongly triggering auto-relisten while the bot was still about to speak the
// real answer. reply_clauses tells the client exactly how many real clauses to expect before
// any of their audio arrives, so the true last one can be identified by index instead of
// guessed at from queue state.
let expectedClauseCount = 0;
let lastClauseQueued = false;

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "reply_clauses") {
    logEntry(msg.clauses.join(" "), "bot");
    expectedClauseCount = msg.clauses.length;
  } else if (msg.type === "audio_clause") {
    if (msg.index === -1) {
      // A new turn's filler just started - invalidate any stale "last clause" flag left over
      // from the previous turn, in case this new filler also finishes before reply_clauses for
      // THIS turn has arrived to reset expectedClauseCount.
      lastClauseQueued = false;
    } else if (msg.index === expectedClauseCount - 1) {
      lastClauseQueued = true;
    }
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
    // Re-open the mic once the bot has TRULY finished speaking (the real last clause has both
    // arrived and finished playing) - not merely whenever the queue happens to be momentarily
    // empty, which can happen mid-reply (see lastClauseQueued above). The engine's own startup
    // lag (see startListening below) then has the whole gap between "reply audio ends" and
    // "person actually starts talking" to complete, instead of eating into it. Only kicks in
    // once mic permission has already been granted once (via an initial manual tap) - never
    // auto-prompts for permission on its own.
    if (hasListenedBefore && lastClauseQueued) startListening();
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
let hasListenedBefore = false;
let accumulatedTranscript = "";

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

// Shared by the manual tap and the auto-relisten-after-reply trigger below, so both go through
// the exact same startup path (and both benefit from onstart gating "Listening" on real
// readiness, not just the call to start()).
function startListening() {
  if (!recognition || listening) return;
  accumulatedTranscript = "";
  micBtn.disabled = true; // briefly, until onstart confirms the engine is actually ready
  micHint.textContent = "Starting…";
  recognition.start();
  hasListenedBefore = true;
}

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  // continuous=false made Chrome end the whole session on the FIRST brief pause mid-sentence,
  // silently truncating anything said after it (e.g. "schedule a meeting for next Wednesday"
  // <pause> "at 3pm" would only ever send the first half) - found from real usage where several
  // turns arrived as obvious sentence fragments. continuous=true keeps listening across pauses;
  // the session now only ends when the user deliberately taps the mic again to stop, or the
  // browser's own silence timeout ends it (see onend - it does NOT auto-relisten itself, only a
  // genuinely new bot reply does, so a quiet room doesn't turn into a listening loop).
  recognition.continuous = true;
  recognition.interimResults = false;
  recognition.lang = "en-US";

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
  // honest signal instead of an optimistic one. Combined with the auto-relisten in playNext()
  // above, the engine now gets a head start on that same warm-up during the reply's own audio
  // playback, rather than only starting once someone taps.
  recognition.onstart = () => {
    setListening(true);
  };

  micBtn.onclick = () => {
    if (listening) {
      recognition.stop();
    } else {
      startListening();
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
