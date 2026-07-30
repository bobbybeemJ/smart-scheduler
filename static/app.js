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

// --- Voice Activity Detection (end-of-turn) ---
// Neither STT path had a real, controlled notion of "the user is done talking": the Web Speech
// engine only stopped on a manual second tap or its own opaque internal silence timeout, and the
// MediaRecorder fallback was pure push-to-talk. This reads actual mic energy in real time (via
// Web Audio's AnalyserNode, on a getUserMedia stream requested purely for metering - independent
// of whatever the STT engine does internally with audio) and ends the turn itself once speech has
// been detected and then stays below the noise floor for VAD_SILENCE_MS. A brief mid-sentence
// breath stays under that duration and does not end the turn; continuous=true below still also
// guards against the recognition engine itself giving up on a short pause. VAD's only
// responsibility is deciding WHEN to call stop() on whichever engine is active - the existing
// onend/onstop handlers still own sending the transcript, unchanged regardless of what triggered
// the stop (manual tap, VAD, or the browser's own timeout as a last-resort backstop).
const VAD_SILENCE_MS = 1400;
const VAD_SPEECH_THRESHOLD = 0.02; // normalized RMS (0-1); tune against real mic/room noise floor
// Cheap, non-LLM stand-in for "does this sound finished": if the last word transcribed before
// silence is a conjunction/filler that's almost never how a real request ends ("...and", "so",
// "um"), the speaker is very likely mid-thought, not done - so silence is given extra room before
// ending the turn. Only extends the wait, never shortens it below VAD_SILENCE_MS, so this can only
// reduce false cutoffs, never introduce a new "ended too early" case that wasn't already possible.
const VAD_TRAILING_GRACE_MS = 700;
const VAD_INCOMPLETE_TRAILING_WORDS = new Set([
  "and", "but", "or", "so", "because", "um", "uh", "like",
  "the", "a", "an", "to", "for", "with", "at", "in", "on",
  "is", "was", "my", "our", "that", "if", "when", "i", "we",
  "let", "lets", "let's", "gonna", "going",
]);
let vadAudioCtx = null;
let vadAnalyser = null;
let vadRafId = null;
let vadHasSpoken = false;
let vadLastSpeechAt = 0;

// accumulatedTranscript is declared further down but already initialized by the time this ever
// runs (tick() only fires asynchronously, well after the whole script has executed top-to-bottom).
function vadSilenceDeadline() {
  const words = accumulatedTranscript.trim().split(/\s+/);
  const last = (words[words.length - 1] || "").toLowerCase().replace(/[^a-z']/g, "");
  return VAD_INCOMPLETE_TRAILING_WORDS.has(last) ? VAD_SILENCE_MS + VAD_TRAILING_GRACE_MS : VAD_SILENCE_MS;
}

function startVAD(stream) {
  stopVAD();
  vadAudioCtx = new (window.AudioContext || window.webkitAudioContext)();
  vadAudioCtx.resume().catch(() => {}); // some browsers start a fresh context suspended
  const source = vadAudioCtx.createMediaStreamSource(stream);
  vadAnalyser = vadAudioCtx.createAnalyser();
  vadAnalyser.fftSize = 512;
  source.connect(vadAnalyser);
  const data = new Uint8Array(vadAnalyser.fftSize);
  vadHasSpoken = false;
  vadLastSpeechAt = 0;

  function tick() {
    vadAnalyser.getByteTimeDomainData(data);
    let sumSquares = 0;
    for (let i = 0; i < data.length; i++) {
      const centered = (data[i] - 128) / 128;
      sumSquares += centered * centered;
    }
    const rms = Math.sqrt(sumSquares / data.length);
    const now = Date.now();
    if (rms > VAD_SPEECH_THRESHOLD) {
      vadHasSpoken = true;
      vadLastSpeechAt = now;
    } else if (vadHasSpoken && now - vadLastSpeechAt > vadSilenceDeadline()) {
      // Triggers stop() on whichever engine is active; its onend/onstop handler calls stopVAD()
      // once the stop actually completes - just don't reschedule another tick from here.
      endTurnFromVAD();
      return;
    }
    vadRafId = requestAnimationFrame(tick);
  }
  vadRafId = requestAnimationFrame(tick);
}

function stopVAD() {
  if (vadRafId) cancelAnimationFrame(vadRafId);
  vadRafId = null;
  if (vadAudioCtx) vadAudioCtx.close().catch(() => {});
  vadAudioCtx = null;
  vadAnalyser = null;
}

function endTurnFromVAD() {
  if (recognition && listening) {
    recognition.stop();
  } else if (mediaRecorder && mediaRecorder.state !== "inactive") {
    mediaRecorder.stop();
  }
}

// --- Speech input: Web Speech API primary, MediaRecorder fallback ---
const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let mediaRecorder = null;
let recordedChunks = [];
let listening = false;
let hasListenedBefore = false;
let accumulatedTranscript = "";
let currentMicStream = null;

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

function releaseMicStream() {
  if (currentMicStream) {
    currentMicStream.getTracks().forEach((track) => track.stop());
    currentMicStream = null;
  }
}

// Shared by the manual tap and the auto-relisten-after-reply trigger below, so both go through
// the exact same startup path (and both benefit from onstart gating "Listening" on real
// readiness, not just the call to start()). getUserMedia is requested here - and awaited -
// before recognition.start(), purely so VAD has a raw audio stream to meter; the Web Speech
// engine still does its own separate internal capture and is unaffected by this stream.
async function startListening() {
  if (!recognition || listening) return;
  accumulatedTranscript = "";
  micBtn.disabled = true; // briefly, until onstart confirms the engine is actually ready
  micHint.textContent = "Starting…";
  hasListenedBefore = true;
  try {
    currentMicStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    logEntry("Microphone permission is required to listen.", "error");
    micBtn.disabled = false;
    micHint.textContent = "Tap the mic to start speaking";
    return;
  }
  startVAD(currentMicStream);
  recognition.start();
}

if (SpeechRecognition) {
  recognition = new SpeechRecognition();
  // continuous=false made Chrome end the whole session on the FIRST brief pause mid-sentence,
  // silently truncating anything said after it (e.g. "schedule a meeting for next Wednesday"
  // <pause> "at 3pm" would only ever send the first half) - found from real usage where several
  // turns arrived as obvious sentence fragments. continuous=true keeps listening across pauses;
  // VAD above now owns ending the turn on real silence, with the browser's own internal timeout
  // remaining only as a last-resort backstop if VAD's stream/AudioContext ever fails.
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
    stopVAD();
    releaseMicStream();
  };
  recognition.onend = () => {
    setListening(false);
    stopVAD();
    releaseMicStream();
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
      currentMicStream = stream;
      recordedChunks = [];
      mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm" });
      mediaRecorder.ondataavailable = (e) => recordedChunks.push(e.data);
      mediaRecorder.onstop = async () => {
        stopVAD();
        const blob = new Blob(recordedChunks, { type: "audio/webm" });
        const base64 = await blobToBase64(blob);
        ws.send(JSON.stringify({ type: "audio_chunk", data_base64: base64, is_final: true }));
        releaseMicStream();
      };
      mediaRecorder.start();
      setListening(true);
      startVAD(stream);
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
