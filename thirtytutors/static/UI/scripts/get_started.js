// ThirtyTutors - landing page (name / native language / API key / model).
// Saves a draft into sessionStorage and hands off to /avatar-select, which
// does the actual profile + conversation creation once an avatar/voice and
// target language are also chosen. Voice enrollment and hands-free
// threshold calibration happen later, on their own dedicated
// /handsfree-setup page (triggered from the learning page's hands-free mic
// button) - not part of this initial onboarding flow.

const firstNameInput = document.getElementById('firstNameInput');
const nativeLanguageInput = document.getElementById('nativeLanguageInput');
const apiKeyInput = document.getElementById('apiKeyInput');
const toggleApiKeyBtn = document.getElementById('toggleApiKeyBtn');
const modelSelect = document.getElementById('modelSelect');
const modelHint = document.getElementById('modelHint');
const nextBtn = document.getElementById('nextBtn');

const DRAFT_KEY = 'landingDraft';
let modelsData = [];

// --- Restore a draft if the user came back from /avatar-select via Back ---

function restoreDraft() {
  const raw = sessionStorage.getItem(DRAFT_KEY);
  if (!raw) return;
  try {
    const draft = JSON.parse(raw);
    firstNameInput.value = draft.name || '';
    nativeLanguageInput.value = draft.native_language || '';
    apiKeyInput.value = draft.api_key || '';
    if (draft.model_name) modelSelect.value = draft.model_name;
  } catch (e) { /* corrupt draft - ignore, start fresh */ }
}

// --- Model list ---

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
modelSelect.addEventListener('change', updateModelHint);

// --- API key visibility toggle (same pattern as the learning page's
// sidebar - kept in sync deliberately, not shared code, since these are
// two separate pages/documents) ---

toggleApiKeyBtn.addEventListener('click', () => {
  const showing = apiKeyInput.type === 'text';
  apiKeyInput.type = showing ? 'password' : 'text';
  toggleApiKeyBtn.textContent = showing ? '👁️' : '🙈';
});

// --- Validation + Next ---

function updateNextEnabled() {
  nextBtn.disabled = !(
    firstNameInput.value.trim() &&
    nativeLanguageInput.value.trim() &&
    apiKeyInput.value.trim()
  );
}
[firstNameInput, nativeLanguageInput, apiKeyInput].forEach((el) => {
  el.addEventListener('input', updateNextEnabled);
});

nextBtn.addEventListener('click', () => {
  const draft = {
    name: firstNameInput.value.trim(),
    native_language: nativeLanguageInput.value.trim(),
    api_key: apiKeyInput.value.trim(),
    model_name: modelSelect.value,
  };
  sessionStorage.setItem(DRAFT_KEY, JSON.stringify(draft));
  window.location.href = '/avatar-select';
});

async function init() {
  // Independent per profile, so these should never carry over from the
  // browser's own form-data memory - autocomplete="off" on the inputs
  // handles most cases, this is the belt-and-suspenders guarantee. Done
  // before restoreDraft() so a genuine Back-navigation draft still wins.
  firstNameInput.value = '';
  nativeLanguageInput.value = '';

  await loadModels();
  restoreDraft();
  updateNextEnabled();
}
init();
