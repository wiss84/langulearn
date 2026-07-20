// LanguLearn - mic capture/recording, audio playback, and the waveform
// visualizer (grouped together since the visualizer reads live state -
// isRecording, the analyser nodes - that recording/playback maintain).

let micContext = null;
let micStream = null;
let workletNode = null;
let micAnalyser = null;
let isRecording = false;

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

async function startRecording() {
  if (!ws || ws.readyState !== WebSocket.OPEN) { showError('Not connected yet.'); return; }
  showError('');
  ws.send(JSON.stringify({ type: 'start_turn' }));

  micContext = new AudioContext({ sampleRate: 16000 });
  // Mic choice is a profile-level setting now, picked on /profiles (see
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
  pcmBuffer = [];
  pcmBufferedSamples = 0;
  workletNode.port.onmessage = (event) => {
    pcmBuffer.push(event.data);
    pcmBufferedSamples += event.data.length;
    flushPcmBuffer(false);
  };
  source.connect(workletNode);

  isRecording = true;
  talkBtn.classList.add('recording');
  talkHint.textContent = 'Listening...';
}

function stopRecording() {
  if (!micStream) return;
  flushPcmBuffer(true);
  micStream.getTracks().forEach((t) => t.stop());
  workletNode && workletNode.disconnect();
  micContext && micContext.close();
  micStream = null;
  isRecording = false;
  talkBtn.classList.remove('recording');

  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'turn_complete' }));
  }
  talkHint.textContent = 'Waiting for reply...';
}

talkBtn.addEventListener('mousedown', startRecording);
talkBtn.addEventListener('mouseup', stopRecording);
talkBtn.addEventListener('mouseleave', () => { if (micStream) stopRecording(); });
talkBtn.addEventListener('touchstart', (e) => { e.preventDefault(); startRecording(); });
talkBtn.addEventListener('touchend', (e) => { e.preventDefault(); stopRecording(); });

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
