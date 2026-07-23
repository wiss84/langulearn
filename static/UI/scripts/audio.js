// LanguLearn - mic capture/recording, audio playback, and the waveform
// visualizer (grouped together since the visualizer reads live state -
// isRecording, the analyser nodes - that recording/playback maintain).

let micContext = null;
let micStream = null;
let workletNode = null;
let micAnalyser = null;
let isRecording = false;

// The mic (getUserMedia + AudioContext + worklet graph) is acquired ONCE
// per page load and kept alive for the whole session, rather than
// opened/closed on every press/release. On Windows, opening a built-in
// mic is a real hardware negotiation each time (visible as the taskbar
// mic indicator appearing/disappearing) and took 4-7s per press - every
// turn was paying that cost. Bluetooth mics don't show this because they
// stay in an "active-ready" state as part of their own connection
// profile, so this was never visible on those. Now startRecording/
// stopRecording only toggle whether captured audio is actually sent
// (isRecording), never whether the mic itself is open - the one-time
// warm-up cost is paid on the FIRST press of a page session, not every
// press. micReadyPromise dedupes concurrent calls (e.g. a fast double
// press) so ensureMicReady's setup work only ever runs once.
let micReadyPromise = null;

let playbackContext = null;
let playbackAnalyser = null;
let playbackBus = null;
let nextPlaybackTime = 0;

// When the avatar drawer (avatarDrawer.js, a separate ES module) has a
// TalkingHead + HeadAudio ready, playback routes through the avatar's own
// AudioContext instead of the standalone one below - HeadAudio has to
// share a context with whatever audio it's analyzing (Web Audio nodes
// can't cross contexts), so this is what makes the avatar's mouth move in
// sync with the actual live conversation audio rather than sitting idle.
// null until the drawer is opened for the first time; once set it stays
// set (the avatar keeps existing even when the drawer is closed again, so
// there's no need to switch back).
let avatarAudioSink = null; // { audioCtx, headaudio }

function registerAvatarAudioSink(audioCtx, headaudio) {
  avatarAudioSink = { audioCtx, headaudio };
  nextPlaybackTime = audioCtx.currentTime; // fresh scheduling clock for the new context
}

// --- Idle -> sleep mood ---
// After 2 minutes with no conversation activity, the avatar's mood is set
// to 'sleep' - a deterministic, Gemini-independent UI state (see
// design_plans/ for why this can't be left to the set_mood tool: the tool
// only fires at Gemini's discretion, so nothing guarantees a call to end
// an idle period). Resuming activity always resets the mood to 'neutral'
// first, before anything round-trips through Gemini - whatever mood
// Gemini reflects for the new turn (if any) naturally overrides this
// afterward. window.setAvatarMood is exposed by avatarDrawer.js and is a
// no-op until the avatar drawer has actually been opened once.
const IDLE_SLEEP_MS = 2 * 60 * 1000;
let idleSleepTimer = null;
let avatarAsleep = false;

function armIdleSleepTimer() {
  if (idleSleepTimer) clearTimeout(idleSleepTimer);
  idleSleepTimer = setTimeout(() => {
    avatarAsleep = true;
    if (window.setAvatarMood) window.setAvatarMood('sleep');
  }, IDLE_SLEEP_MS);
}

function noteConversationActivity() {
  if (avatarAsleep) {
    avatarAsleep = false;
    if (window.setAvatarMood) window.setAvatarMood('neutral');
  }
  armIdleSleepTimer();
}

armIdleSleepTimer(); // start the clock at page load too

const CHUNK_SAMPLES = 640; // ~40ms at 16kHz
let pcmBuffer = [];
let pcmBufferedSamples = 0;

// --- Audio playback ---

function ensurePlaybackContext() {
  if (avatarAudioSink || playbackContext) return;
  playbackContext = new AudioContext({ sampleRate: 24000 });
  playbackAnalyser = playbackContext.createAnalyser();
  playbackAnalyser.fftSize = 256;
  playbackBus = playbackContext.createGain();
  playbackBus.connect(playbackAnalyser);
  playbackAnalyser.connect(playbackContext.destination);
  nextPlaybackTime = playbackContext.currentTime;
}

function base64ToInt16(b64) {
  const binary = atob(b64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

function playAudioChunk(b64data) {
  ensurePlaybackContext();
  const ctx = avatarAudioSink ? avatarAudioSink.audioCtx : playbackContext;

  const int16 = base64ToInt16(b64data);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

  const buffer = ctx.createBuffer(1, float32.length, 24000);
  buffer.copyToChannel(float32, 0);

  const source = ctx.createBufferSource();
  source.buffer = buffer;
  if (avatarAudioSink) {
    source.connect(avatarAudioSink.headaudio); // viseme analysis -> mouth movement
    source.connect(ctx.destination); // actually audible
  } else {
    source.connect(playbackBus);
  }

  const startAt = Math.max(nextPlaybackTime, ctx.currentTime);
  source.start(startAt);
  nextPlaybackTime = startAt + buffer.duration;
}

function isTutorSpeaking() {
  const ctx = avatarAudioSink ? avatarAudioSink.audioCtx : playbackContext;
  return !!ctx && ctx.currentTime < nextPlaybackTime;
}

// --- Recording ---

function float32ToInt16Base64(float32Array) {
  const int16 = new Int16Array(float32Array.length);
  for (let i = 0; i < float32Array.length; i++) {
    const s = Math.max(-1, Math.min(1, float32Array[i]));
    int16[i] = s < 0 ? s * 32768 : s * 32767;
  }
  const bytes = new Uint8Array(int16.buffer);
  let binary = '';
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  return btoa(binary);
}

function flushPcmBuffer(force) {
  while (pcmBufferedSamples >= CHUNK_SAMPLES || (force && pcmBufferedSamples > 0)) {
    const combined = new Float32Array(pcmBufferedSamples);
    let offset = 0;
    for (const chunk of pcmBuffer) { combined.set(chunk, offset); offset += chunk.length; }
    const takeCount = force ? pcmBufferedSamples : CHUNK_SAMPLES;
    const toSend = combined.slice(0, takeCount);
    const remainder = combined.slice(takeCount);
    ws.send(JSON.stringify({ type: 'audio_chunk', data: float32ToInt16Base64(toSend) }));
    pcmBuffer = remainder.length ? [remainder] : [];
    pcmBufferedSamples = remainder.length;
    if (force) break;
  }
}

// Acquires the mic + builds the capture graph exactly once per page
// session. Safe to call repeatedly - subsequent calls just await the same
// in-flight (or already-resolved) promise, so a fast double-press or a
// spacebar auto-repeat can never open a second stream.
function ensureMicReady() {
  if (micReadyPromise) return micReadyPromise;

  micReadyPromise = (async () => {
    micContext = new AudioContext({ sampleRate: 16000 });
    // Mic choice is a profile-level setting, picked on /profiles (see
    // profileDetail.js) rather than in a sidebar control here - so this
    // reads straight from the loaded profile instead of a DOM element.
    const deviceId = currentProfile && currentProfile.mic_device_id;
    const constraints = deviceId ? { audio: { deviceId: { exact: deviceId } } } : { audio: true };
    micStream = await navigator.mediaDevices.getUserMedia(constraints);
    await micContext.audioWorklet.addModule('/pcm-processor.js');

    const source = micContext.createMediaStreamSource(micStream);
    micAnalyser = micContext.createAnalyser();
    micAnalyser.fftSize = 256;
    source.connect(micAnalyser);

    workletNode = new AudioWorkletNode(micContext, 'pcm-capture-processor');
    workletNode.port.onmessage = (event) => {
      if (!isRecording) return; // mic stays open between turns; only forward while actually recording
      pcmBuffer.push(event.data);
      pcmBufferedSamples += event.data.length;
      flushPcmBuffer(false);
    };
    source.connect(workletNode);
  })();

  return micReadyPromise;
}

async function startRecording() {
  if (isRecording) return;
  if (!ws || ws.readyState !== WebSocket.OPEN) { showError('Not connected yet.'); return; }
  showError('');

  try {
    await ensureMicReady();
  } catch (e) {
    showError('Could not access the microphone.');
    console.error(e);
    micReadyPromise = null; // allow a retry on the next press
    return;
  }

  noteConversationActivity();
  pcmBuffer = [];
  pcmBufferedSamples = 0;
  isRecording = true;
  ws.send(JSON.stringify({ type: 'start_turn' }));
  talkBtn.classList.add('recording');
  talkHint.textContent = 'Listening...';
}

function stopRecording() {
  if (!isRecording) return;
  isRecording = false;
  flushPcmBuffer(true);
  talkBtn.classList.remove('recording');

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'turn_complete' }));
  }
  talkHint.textContent = 'Waiting for reply...';
}

talkBtn.addEventListener('mousedown', startRecording);
talkBtn.addEventListener('mouseup', stopRecording);
talkBtn.addEventListener('mouseleave', () => { if (isRecording) stopRecording(); });
talkBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); });
talkBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopRecording(); });

// --- Spacebar push-to-talk ---
// Mirrors the talk button exactly (same startRecording/stopRecording),
// guarded so it doesn't fire while the user is typing in a text field
// (e.g. renaming a conversation) and doesn't re-trigger on key-repeat
// while held down.
function isTypingTarget(el) {
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable;
}

document.addEventListener('keydown', (e) => {
  if (e.code !== 'Space' || e.repeat || isTypingTarget(e.target)) return;
  e.preventDefault(); // stop the page from scrolling on spacebar
  startRecording();
});

document.addEventListener('keyup', (e) => {
  if (e.code !== 'Space' || isTypingTarget(e.target)) return;
  e.preventDefault();
  stopRecording();
});

// Release the mic on page unload so it doesn't stay flagged "in use"
// after navigating away or closing the app.
window.addEventListener('beforeunload', () => {
  if (micStream) micStream.getTracks().forEach((t) => t.stop());
});

// --- Waveform visualizer ---

function resizeCanvas() {
  const dpr = window.devicePixelRatio || 1;
  const rect = waveformCanvas.getBoundingClientRect();
  waveformCanvas.width = rect.width * dpr;
  waveformCanvas.height = rect.height * dpr;
  waveformCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', resizeCanvas);

function drawWaveform() {
  requestAnimationFrame(drawWaveform);
  const rect = waveformCanvas.getBoundingClientRect();
  const w = rect.width, h = rect.height;
  waveformCtx.clearRect(0, 0, w, h);

  const activeAnalyser = isRecording ? micAnalyser : (isTutorSpeaking() ? playbackAnalyser : null);
  const barCount = 48;
  const barWidth = w / barCount;
  const mid = h / 2;

  waveformCtx.fillStyle = isRecording ? '#c1403d' : '#4a5568';

  if (!activeAnalyser) {
    const t = performance.now() / 1000;
    for (let i = 0; i < barCount; i++) {
      const amp = 2 + Math.sin(t * 1.2 + i * 0.3) * 1.5;
      waveformCtx.fillRect(i * barWidth + 1, mid - amp / 2, barWidth - 2, amp);
    }
    return;
  }

  const data = new Uint8Array(activeAnalyser.frequencyBinCount);
  activeAnalyser.getByteFrequencyData(data);
  const step = Math.floor(data.length / barCount) || 1;

  for (let i = 0; i < barCount; i++) {
    const v = data[i * step] / 255;
    const barHeight = Math.max(2, v * h * 0.9);
    waveformCtx.fillRect(i * barWidth + 1, mid - barHeight / 2, barWidth - 2, barHeight);
  }
}