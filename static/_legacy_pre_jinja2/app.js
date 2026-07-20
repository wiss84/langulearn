// LanguLearn - frontend logic

const talkBtn = document.getElementById('talkBtn');
const talkHint = document.getElementById('talkHint');
const connectionDot = document.getElementById('connectionDot');
const errorBanner = document.getElementById('errorBanner');
const transcriptArea = document.getElementById('transcriptArea');
const emptyState = document.getElementById('emptyState');
const micSelect = document.getElementById('micSelect');
const refreshMicsBtn = document.getElementById('refreshMicsBtn');
const genderSelect = document.getElementById('genderSelect');
const voiceSelect = document.getElementById('voiceSelect');
const modelSelect = document.getElementById('modelSelect');
const modelHint = document.getElementById('modelHint');
const nativeLanguageInput = document.getElementById('nativeLanguageInput');
const targetLanguageInput = document.getElementById('targetLanguageInput');
const currentProfileName = document.getElementById('currentProfileName');
const switchProfileBtn = document.getElementById('switchProfileBtn');
const profileModalOverlay = document.getElementById('profileModalOverlay');
const profileList = document.getElementById('profileList');
const newProfileName = document.getElementById('newProfileName');
const createProfileBtn = document.getElementById('createProfileBtn');
const waveformCanvas = document.getElementById('waveform');
const waveformCtx = waveformCanvas.getContext('2d');
const conversationList = document.getElementById('conversationList');
const newConversationBtn = document.getElementById('newConversationBtn');
const sessionStatusText = document.getElementById('sessionStatusText');
const viewNotesBtn = document.getElementById('viewNotesBtn');
const notesModalOverlay = document.getElementById('notesModalOverlay');
const notesModalSubtitle = document.getElementById('notesModalSubtitle');
const notesVocabList = document.getElementById('notesVocabList');
const notesLessonList = document.getElementById('notesLessonList');
const closeNotesBtn = document.getElementById('closeNotesBtn');

let sessionStatusTimer = null;

let ws = null;
let reconnectTimer = null;
let reconnectDebounceTimer = null;

let currentProfile = null;
let voicesData = [];
let modelsData = [];

let conversationsCache = [];
let currentConversationId = null;

let micContext = null;
let micStream = null;
let workletNode = null;
let micAnalyser = null;
let isRecording = false;

let playbackContext = null;
let playbackAnalyser = null;
let playbackBus = null;
let nextPlaybackTime = 0;

// One "in-progress" bubble per speaker per turn - transcripts stream in as
// several small chunks (from both Gemini's output_audio_transcription for
// the tutor and input_audio_transcription for the student), so these get
// appended to rather than each chunk becoming its own bubble.
let activeBubbles = { mine: null, tutor: null };

const DEFAULT_VOICE = 'Kore';
const CHUNK_SAMPLES = 640; // ~40ms at 16kHz
let pcmBuffer = [];
let pcmBufferedSamples = 0;

// --- Errors / status ---

function showError(text) {
  if (!text) { errorBanner.classList.remove('visible'); errorBanner.textContent = ''; return; }
  errorBanner.textContent = text;
  errorBanner.classList.add('visible');
}

function setConnectionState(state) {
  connectionDot.className = state === 'connected' ? 'connected' : state === 'error' ? 'error' : '';
}

function showSessionStatus(resumed) {
  sessionStatusText.textContent = resumed ? '\u21BB resumed session' : '\u2726 fresh session';
  sessionStatusText.classList.add('visible');
  clearTimeout(sessionStatusTimer);
  sessionStatusTimer = setTimeout(() => sessionStatusText.classList.remove('visible'), 4000);
}

// --- Transcript ---

function appendOrCreateBubble(who, textChunk) {
  if (activeBubbles[who]) {
    activeBubbles[who].textSpan.textContent += textChunk;
  } else {
    if (emptyState.isConnected) emptyState.remove();
    const bubble = document.createElement('div');
    bubble.className = `bubble ${who === 'mine' ? 'mine' : 'tutor'}`;
    const label = document.createElement('span');
    label.className = 'speaker-label';
    label.textContent = who === 'mine' ? (currentProfile ? currentProfile.name : 'You') : 'Tutor';
    const body = document.createElement('span');
    body.textContent = textChunk;
    bubble.appendChild(label);
    bubble.appendChild(body);
    transcriptArea.appendChild(bubble);
    activeBubbles[who] = { el: bubble, textSpan: body };
  }
  transcriptArea.scrollTop = transcriptArea.scrollHeight;
}

function finalizeTurnBubbles() {
  activeBubbles.mine = null;
  activeBubbles.tutor = null;
}

function renderHistoryBubble(who, text) {
  const bubble = document.createElement('div');
  bubble.className = `bubble ${who === 'mine' ? 'mine' : 'tutor'}`;
  const label = document.createElement('span');
  label.className = 'speaker-label';
  label.textContent = who === 'mine' ? (currentProfile ? currentProfile.name : 'You') : 'Tutor';
  const body = document.createElement('span');
  body.textContent = text;
  bubble.appendChild(label);
  bubble.appendChild(body);
  transcriptArea.appendChild(bubble);
}

function renderConversationTranscript(turns) {
  finalizeTurnBubbles();
  transcriptArea.innerHTML = '';
  if (!turns || turns.length === 0) {
    const fresh = document.createElement('div');
    fresh.id = 'emptyState';
    fresh.textContent = 'Hold the button below and start speaking.';
    transcriptArea.appendChild(fresh);
    return;
  }
  turns.forEach((t) => renderHistoryBubble(t.role === 'user' ? 'mine' : 'tutor', t.text));
  transcriptArea.scrollTop = transcriptArea.scrollHeight;
}

// --- Reconnect debounce (avoid reconnecting once per keystroke/click when
// several settings change in quick succession) ---

function scheduleReconnect() {
  clearTimeout(reconnectDebounceTimer);
  reconnectDebounceTimer = setTimeout(reconnectWebSocket, 500);
}

// --- Voices ---

async function loadVoices() {
  const res = await fetch('/api/voices');
  const data = await res.json();
  voicesData = data.voices;
  renderVoiceOptionsForGender(genderSelect.value || 'Female');
}

function renderVoiceOptionsForGender(gender) {
  const previouslySelected = voiceSelect.value;
  const filtered = voicesData.filter((v) => v.gender === gender);
  voiceSelect.innerHTML = '';
  filtered.forEach((v) => {
    const opt = document.createElement('option');
    opt.value = v.name;
    opt.textContent = `${v.name} - ${v.descriptor}`;
    voiceSelect.appendChild(opt);
  });
  if (filtered.some((v) => v.name === previouslySelected)) {
    voiceSelect.value = previouslySelected;
  } else if (filtered.length > 0) {
    voiceSelect.value = filtered[0].name;
  }
}

function findVoiceGender(voiceName) {
  const v = voicesData.find((x) => x.name === voiceName);
  return v ? v.gender : 'Female';
}

genderSelect.addEventListener('change', () => {
  renderVoiceOptionsForGender(genderSelect.value);
  if (!currentProfile || !currentConversationId) return;

  const genderVoices = voicesData.filter((v) => v.gender === genderSelect.value);
  let voiceToUse = voiceSelect.value;
  if (!genderVoices.some((v) => v.name === voiceToUse)) {
    voiceToUse = genderVoices.length > 0 ? genderVoices[0].name : DEFAULT_VOICE;
  }
  voiceSelect.value = voiceToUse;

  persistConversationField({ voice_name: voiceToUse });
  scheduleReconnect();
});

voiceSelect.addEventListener('change', () => {
  if (!currentProfile || !currentConversationId) return;
  persistConversationField({ voice_name: voiceSelect.value });
  scheduleReconnect();
});

// --- Models ---

async function loadModels() {
  const res = await fetch('/api/models');
  const data = await res.json();
  modelsData = data.models;
  modelSelect.innerHTML = '';
  modelsData.forEach((m) => {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = m.label;
    modelSelect.appendChild(opt);
  });
  modelSelect.value = data.default;
  updateModelHint();
}

function updateModelHint() {
  const m = modelsData.find((x) => x.id === modelSelect.value);
  if (!m) { modelHint.textContent = ''; return; }
  modelHint.textContent = `${m.rate_limit_note}${m.supports_affective_dialog ? ' - supports emotional tone (affective dialog)' : ' - lower latency, no affective dialog'}`;
}

modelSelect.addEventListener('change', () => {
  updateModelHint();
  if (!currentProfile || !currentConversationId) return;
  persistConversationField({ model_name: modelSelect.value });
  scheduleReconnect();
});

// --- Languages ---

nativeLanguageInput.addEventListener('change', () => {
  if (!currentProfile || !currentConversationId) return;
  const value = nativeLanguageInput.value.trim() || 'English';
  nativeLanguageInput.value = value;
  persistConversationField({ native_language: value });
  scheduleReconnect();
});
targetLanguageInput.addEventListener('change', () => {
  if (!currentProfile || !currentConversationId) return;
  const value = targetLanguageInput.value.trim() || 'Polish';
  targetLanguageInput.value = value;
  persistConversationField({ target_language: value });
  scheduleReconnect();
});

// --- Microphones (profile-level - one device at a time regardless of
// which conversation is active) ---

async function loadMics() {
  try {
    const tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    tempStream.getTracks().forEach((t) => t.stop());
  } catch (e) {
    showError('Microphone permission was denied - allow it to pick a device and record.');
    return;
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  const mics = devices.filter((d) => d.kind === 'audioinput');
  const previouslySelected = micSelect.value;
  micSelect.innerHTML = '<option value="">Default microphone</option>';
  mics.forEach((d, i) => {
    const opt = document.createElement('option');
    opt.value = d.deviceId;
    opt.textContent = d.label || `Microphone ${i + 1}`;
    micSelect.appendChild(opt);
  });
  if (mics.some((d) => d.deviceId === previouslySelected)) {
    micSelect.value = previouslySelected;
  } else if (currentProfile && currentProfile.mic_device_id && mics.some((d) => d.deviceId === currentProfile.mic_device_id)) {
    micSelect.value = currentProfile.mic_device_id;
  } else if (currentProfile && currentProfile.mic_label) {
    const match = mics.find((d) => d.label === currentProfile.mic_label);
    if (match) micSelect.value = match.deviceId;
  }
}

refreshMicsBtn.addEventListener('click', loadMics);

micSelect.addEventListener('change', () => {
  if (!currentProfile) return;
  const selectedOption = micSelect.options[micSelect.selectedIndex];
  currentProfile.mic_device_id = micSelect.value || null;
  currentProfile.mic_label = micSelect.value ? selectedOption.textContent : null;
  persistProfileField({ mic_device_id: currentProfile.mic_device_id, mic_label: currentProfile.mic_label });
  // Mic choice is client-side only - no reconnect needed.
});

// --- Profiles ---

async function fetchProfileList() {
  const res = await fetch('/api/profiles');
  const data = await res.json();
  return data.profiles;
}

async function fetchProfile(id) {
  const res = await fetch(`/api/profiles/${id}`);
  if (!res.ok) throw new Error('Profile not found');
  return res.json();
}

async function createProfile(name) {
  const res = await fetch('/api/profiles', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  return res.json();
}

function persistProfileField(fields) {
  if (!currentProfile) return;
  fetch(`/api/profiles/${currentProfile.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  }).catch(() => {});
}

async function deleteProfile(id) {
  const res = await fetch(`/api/profiles/${id}`, { method: 'DELETE' });
  if (!res.ok) throw new Error('Failed to delete profile');
}

async function openProfileModal() {
  const profiles = await fetchProfileList();
  profileList.innerHTML = '';
  profiles.forEach((p) => {
    const row = document.createElement('div');
    row.className = 'profile-row';

    const btn = document.createElement('button');
    btn.className = 'profile-option';
    btn.type = 'button';
    btn.textContent = p.name;
    btn.addEventListener('click', () => selectProfile(p.id));

    const delBtn = document.createElement('button');
    delBtn.className = 'profile-delete-btn';
    delBtn.type = 'button';
    delBtn.title = 'Delete profile';
    delBtn.textContent = '×';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete profile "${p.name}"?`)) return;
      await deleteProfile(p.id);
      if (currentProfile && currentProfile.id === p.id) {
        localStorage.removeItem('tutorProfileId');
        currentProfile = null;
        currentConversationId = null;
        currentProfileName.textContent = '';
        conversationList.innerHTML = '';
      }
      openProfileModal();
    });

    row.appendChild(btn);
    row.appendChild(delBtn);
    profileList.appendChild(row);
  });
  profileModalOverlay.classList.add('visible');
  newProfileName.value = '';
}

function closeProfileModal() {
  profileModalOverlay.classList.remove('visible');
}

async function selectProfile(id) {
  const profile = await fetchProfile(id);
  localStorage.setItem('tutorProfileId', id);
  await applyProfile(profile);
  closeProfileModal();
}

async function applyProfile(profile) {
  currentProfile = profile;
  currentConversationId = null;
  currentProfileName.textContent = profile.name;

  await loadMics();

  await loadConversationsForProfile();
}

switchProfileBtn.addEventListener('click', openProfileModal);

createProfileBtn.addEventListener('click', async () => {
  const name = newProfileName.value.trim();
  if (!name) return;
  const profile = await createProfile(name);
  localStorage.setItem('tutorProfileId', profile.id);
  await applyProfile(profile);
  closeProfileModal();
});
newProfileName.addEventListener('keydown', (e) => {
  if (e.key === 'Enter') createProfileBtn.click();
});

// --- Conversations ---

async function fetchConversationList(profileId) {
  const res = await fetch(`/api/profiles/${profileId}/conversations`);
  if (!res.ok) throw new Error('Failed to load conversations');
  return res.json();
}

async function fetchConversationDetail(profileId, conversationId) {
  const res = await fetch(`/api/profiles/${profileId}/conversations/${conversationId}`);
  if (!res.ok) throw new Error('Conversation not found');
  return res.json();
}

async function createConversationApi(profileId, config, name) {
  const res = await fetch(`/api/profiles/${profileId}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...config, name: name || undefined }),
  });
  return res.json();
}

function persistConversationField(fields) {
  if (!currentProfile || !currentConversationId) return;
  fetch(`/api/profiles/${currentProfile.id}/conversations/${currentConversationId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(fields),
  }).catch(() => {});
}

async function renameConversationApi(id, name) {
  await fetch(`/api/profiles/${currentProfile.id}/conversations/${id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

async function deleteConversationApi(id) {
  await fetch(`/api/profiles/${currentProfile.id}/conversations/${id}`, { method: 'DELETE' });
}

function renderConversationList(list, activeId) {
  conversationList.innerHTML = '';
  list.forEach((c) => {
    const row = document.createElement('div');
    row.className = 'conv-row';

    const btn = document.createElement('button');
    btn.className = 'conv-option' + (c.id === activeId ? ' active' : '');
    btn.type = 'button';
    btn.textContent = c.name || 'Conversation';
    btn.title = c.name || 'Conversation';
    btn.addEventListener('click', () => {
      if (c.id !== currentConversationId) selectConversation(c.id);
    });

    const renameBtn = document.createElement('button');
    renameBtn.className = 'conv-rename-btn';
    renameBtn.type = 'button';
    renameBtn.title = 'Rename conversation';
    renameBtn.textContent = '\u270E';
    renameBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const name = prompt('Rename conversation', c.name || '');
      if (name === null) return;
      const trimmed = name.trim();
      if (!trimmed) return;
      await renameConversationApi(c.id, trimmed);
      await loadConversationsForProfile();
    });

    const delBtn = document.createElement('button');
    delBtn.className = 'conv-delete-btn';
    delBtn.type = 'button';
    delBtn.title = 'Delete conversation';
    delBtn.textContent = '×';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete conversation "${c.name || 'Conversation'}"? This cannot be undone.`)) return;
      await deleteConversationApi(c.id);
      if (currentConversationId === c.id) currentConversationId = null;
      await loadConversationsForProfile();
    });

    row.appendChild(btn);
    row.appendChild(renameBtn);
    row.appendChild(delBtn);
    conversationList.appendChild(row);
  });
}

async function loadConversationsForProfile() {
  if (!currentProfile) return;
  const data = await fetchConversationList(currentProfile.id);
  conversationsCache = data.conversations || [];
  if (conversationsCache.length === 0) return; // shouldn't happen - backend always ensures one

  const activeId = data.active_conversation_id;
  const target = conversationsCache.find((c) => c.id === activeId) || conversationsCache[0];
  renderConversationList(conversationsCache, target.id);
  await selectConversation(target.id);
}

function applyConversationConfigToControls(config) {
  const savedVoiceName = config.voice_name || DEFAULT_VOICE;
  const gender = findVoiceGender(savedVoiceName);
  genderSelect.value = gender;
  renderVoiceOptionsForGender(gender);

  const genderVoices = voicesData.filter((v) => v.gender === gender);
  const voiceToUse = genderVoices.some((v) => v.name === savedVoiceName)
    ? savedVoiceName
    : (genderVoices.length > 0 ? genderVoices[0].name : DEFAULT_VOICE);
  voiceSelect.value = voiceToUse;

  if (modelsData.length) modelSelect.value = config.model_name || modelsData[0].id;
  updateModelHint();

  nativeLanguageInput.value = config.native_language || 'English';
  targetLanguageInput.value = config.target_language || 'Polish';
}

async function selectConversation(id) {
  const conv = await fetchConversationDetail(currentProfile.id, id);
  currentConversationId = id;
  applyConversationConfigToControls(conv.config || {});
  renderConversationTranscript(conv.turns || []);
  renderConversationList(conversationsCache, id);

  if (ws) {
    ws.manualClose = true;
    ws.close();
  }
  connectWebSocket();
}

newConversationBtn.addEventListener('click', async () => {
  if (!currentProfile) return;
  const config = {
    voice_name: voiceSelect.value,
    native_language: nativeLanguageInput.value.trim() || 'English',
    target_language: targetLanguageInput.value.trim() || 'Polish',
    model_name: modelSelect.value,
  };
  await createConversationApi(currentProfile.id, config);
  await loadConversationsForProfile();
});

// --- Conversation notes (vocab/mistakes + lesson log) ---

function formatTimestamp(value) {
  if (!value) return '';
  // ts on vocab_mistakes rows is unix seconds; ts on lesson_log rows is an
  // ISO string - Date() handles both, but only multiply the numeric case.
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function renderNotesList(container, items, emptyText, renderItem) {
  container.innerHTML = '';
  if (!items || items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'notes-empty';
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }
  items.forEach((item) => container.appendChild(renderItem(item)));
}

async function openNotesModal() {
  if (!currentProfile || !currentConversationId) return;
  const conv = conversationsCache.find((c) => c.id === currentConversationId);
  notesModalSubtitle.textContent = conv ? conv.name : '';
  notesVocabList.innerHTML = '';
  notesLessonList.innerHTML = '';
  notesModalOverlay.classList.add('visible');

  const res = await fetch(`/api/profiles/${currentProfile.id}/conversations/${currentConversationId}/notes`);
  if (!res.ok) return;
  const data = await res.json();

  renderNotesList(notesVocabList, data.vocab_mistakes, 'Nothing tracked yet - this fills in as the rolling summary runs.', (v) => {
    const el = document.createElement('div');
    el.className = 'notes-item';
    const title = document.createElement('div');
    title.className = 'notes-item-title';
    title.textContent = v.term;
    el.appendChild(title);
    if (v.note) {
      const note = document.createElement('div');
      note.textContent = v.note;
      el.appendChild(note);
    }
    const meta = document.createElement('div');
    meta.className = 'notes-item-meta';
    meta.textContent = `seen ${v.occurrences}x - last ${formatTimestamp(v.last_seen_ts)}`;
    el.appendChild(meta);
    return el;
  });

  renderNotesList(notesLessonList, data.lesson_log, 'No lesson log entries yet.', (l) => {
    const el = document.createElement('div');
    el.className = 'notes-item';
    const meta = document.createElement('div');
    meta.className = 'notes-item-meta';
    meta.textContent = formatTimestamp(l.ts);
    el.appendChild(meta);
    const body = document.createElement('div');
    body.textContent = l.summary;
    el.appendChild(body);
    return el;
  });
}

function closeNotesModal() {
  notesModalOverlay.classList.remove('visible');
}

viewNotesBtn.addEventListener('click', openNotesModal);
closeNotesBtn.addEventListener('click', closeNotesModal);
notesModalOverlay.addEventListener('click', (e) => {
  if (e.target === notesModalOverlay) closeNotesModal();
});

// --- WebSocket ---

function connectWebSocket() {
  setConnectionState('connecting');

  // Each socket tracks its own manualClose flag and is compared against the
  // current `ws` before acting on any event, instead of relying on one
  // shared `manualClose` boolean. The old code set manualClose = true,
  // called ws.close() (async - it doesn't close immediately), then reset
  // manualClose = false and opened a new socket right away. When the OLD
  // socket's onclose eventually fired, it saw manualClose already back to
  // false and scheduled its own reconnect - racing with the new connection
  // that had already been opened. That produced the connect/disconnect
  // loop (red/green dot flicker) on model/voice/language/conversation
  // switches, and could open two Live sessions for the same conversation
  // at once (surfacing upstream as repeated 1008 errors).
  const socket = new WebSocket(`ws://${location.host}/ws/session`);
  socket.manualClose = false;
  ws = socket;

  socket.onopen = () => {
    if (socket !== ws) return; // superseded by a newer connection
    setConnectionState('connected');
    showError('');
    socket.send(JSON.stringify({
      type: 'init',
      profile_id: currentProfile ? currentProfile.id : null,
      profile_name: currentProfile ? currentProfile.name : 'the student',
      conversation_id: currentProfile ? currentConversationId : null,
      voice_name: voiceSelect.value,
      native_language: nativeLanguageInput.value,
      target_language: targetLanguageInput.value,
      model_name: modelSelect.value,
    }));
  };

  socket.onclose = () => {
    if (socket !== ws) return; // stale socket already superseded - don't double-reconnect
    setConnectionState('error');
    if (!socket.manualClose) {
      reconnectTimer = setTimeout(connectWebSocket, 2000);
    }
  };

  socket.onerror = () => {
    if (socket === ws) setConnectionState('error');
  };

  socket.onmessage = (event) => {
    if (socket !== ws) return; // ignore messages from a superseded connection
    const msg = JSON.parse(event.data);
    if (msg.type === 'audio') {
      playAudioChunk(msg.data);
    } else if (msg.type === 'transcript_in') {
      appendOrCreateBubble('mine', msg.text);
    } else if (msg.type === 'transcript_out') {
      appendOrCreateBubble('tutor', msg.text);
    } else if (msg.type === 'turn_complete') {
      finalizeTurnBubbles();
      talkHint.textContent = 'Hold to speak';
    } else if (msg.type === 'session_status') {
      showSessionStatus(msg.resumed);
    } else if (msg.type === 'error') {
      showError(msg.message);
    }
  };
}

function reconnectWebSocket() {
  clearTimeout(reconnectTimer);
  if (ws) {
    ws.manualClose = true;
    ws.close();
  }
  finalizeTurnBubbles();
  connectWebSocket();
}

// --- Audio playback ---

function ensurePlaybackContext() {
  if (playbackContext) return;
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
  const int16 = base64ToInt16(b64data);
  const float32 = new Float32Array(int16.length);
  for (let i = 0; i < int16.length; i++) float32[i] = int16[i] / 32768;

  const buffer = playbackContext.createBuffer(1, float32.length, 24000);
  buffer.copyToChannel(float32, 0);

  const source = playbackContext.createBufferSource();
  source.buffer = buffer;
  source.connect(playbackBus);

  const startAt = Math.max(nextPlaybackTime, playbackContext.currentTime);
  source.start(startAt);
  nextPlaybackTime = startAt + buffer.duration;
}

function isTutorSpeaking() {
  return !!playbackContext && playbackContext.currentTime < nextPlaybackTime;
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
  const constraints = micSelect.value ? { audio: { deviceId: { exact: micSelect.value } } } : { audio: true };
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

// --- Init ---

async function init() {
  resizeCanvas();
  requestAnimationFrame(drawWaveform);
  await loadVoices();
  await loadModels();
  await loadMics();

  const savedId = localStorage.getItem('tutorProfileId');
  if (savedId) {
    try {
      const profile = await fetchProfile(savedId);
      await applyProfile(profile);
      return;
    } catch (e) {
      localStorage.removeItem('tutorProfileId');
    }
  }
  openProfileModal();
}

init();
