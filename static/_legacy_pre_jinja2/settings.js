// LanguLearn - sidebar settings: API key, voices, models, languages,
// microphones. Every control here belongs to the *current profile*
// (API key, microphone) or the *current conversation* (voice/model/
// language), except where noted.

// --- API key (profile-level - each profile carries its own Gemini key;
// there's no shared/env fallback, so a profile without one can't connect -
// see the error the server sends back over the websocket) ---

toggleApiKeyBtn.addEventListener('click', () => {
  const showing = apiKeyInput.type === 'text';
  apiKeyInput.type = showing ? 'password' : 'text';
  toggleApiKeyBtn.textContent = showing ? '\u{1F441}\u{FE0F}' : '\u{1F648}';
});

apiKeyInput.addEventListener('change', () => {
  if (!currentProfile) return;
  const value = apiKeyInput.value.trim();
  currentProfile.api_key = value || null;
  persistProfileField({ api_key: currentProfile.api_key });
  scheduleReconnect(); // new key only takes effect on the next connect
});

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
