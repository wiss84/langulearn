// LanguLearn - status/error banners + transcript bubble rendering.

let sessionStatusTimer = null;

// One "in-progress" bubble per speaker per turn - transcripts stream in as
// several small chunks (from both Gemini's output_audio_transcription for
// the tutor and input_audio_transcription for the student), so these get
// appended to rather than each chunk becoming its own bubble.
let activeBubbles = { mine: null, tutor: null };

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
