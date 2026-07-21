// LanguLearn - avatar + voice selection page. Renders the female/male
// tutor grids from /api/voices, gates tiles on /api/avatars (only voices
// with a real .glb get a live preview - the rest show "Coming soon" until
// more avatars exist), previews the selection via avatarPreview.js.
//
// Two modes, both landing on this same page:
//  - New profile (no ?profile_id in the URL): came from /landing, which
//    stashed name/native_language/api_key/model_name in sessionStorage.
//    Next creates the profile AND its first conversation.
//  - Existing profile (?profile_id=<id> in the URL): came from "+ Learn a
//    new language" in a profile's detail modal on /profiles. Next only
//    creates a new conversation under that profile - no profile fields to
//    collect, and Back returns to /profiles (reopening that same profile's
//    detail modal) rather than /landing.

import { initAvatarHead, loadAvatarAndPlaySample, playGreeting } from '/UI/scripts/avatarPreview.js';

const femaleGrid = document.getElementById('femaleGrid');
const maleGrid = document.getElementById('maleGrid');
const avatarPreviewEl = document.getElementById('avatarPreview');
const avatarPreviewHint = document.getElementById('avatarPreviewHint');
const avatarVoiceMeta = document.getElementById('avatarVoiceMeta');
const targetLanguageInput = document.getElementById('targetLanguageInput');
const backBtn = document.getElementById('avatarBackBtn');
const nextBtn = document.getElementById('avatarNextBtn');

const DRAFT_KEY = 'landingDraft';
const existingProfileId = new URLSearchParams(window.location.search).get('profile_id');

let selectedVoice = null;
let availableAvatars = [];
let voicesData = [];

function updateNextEnabled() {
  nextBtn.disabled = !(selectedVoice && targetLanguageInput.value.trim());
}

function renderGrid(container, voices) {
  container.innerHTML = '';
  voices.forEach((v) => {
    const isAvailable = availableAvatars.includes(v.name);

    const tile = document.createElement('button');
    tile.type = 'button';
    tile.className = 'tutor-tile' + (isAvailable ? '' : ' coming-soon');
    tile.disabled = !isAvailable;
    tile.dataset.voice = v.name;

    const img = document.createElement('img');
    img.src = `/photos/${v.name}.webp`;
    img.alt = v.name;

    const fallback = document.createElement('div');
    fallback.className = 'tutor-tile-fallback';
    fallback.textContent = v.name[0];
    img.addEventListener('error', () => {
      img.remove();
      fallback.style.display = 'flex';
    });

    const label = document.createElement('span');
    label.className = 'tutor-name';
    label.textContent = v.name;

    tile.appendChild(img);
    tile.appendChild(fallback);
    tile.appendChild(label);

    if (isAvailable) {
      tile.addEventListener('click', () => selectAvatar(v.name, tile));
    } else {
      const badge = document.createElement('span');
      badge.className = 'tutor-badge';
      badge.textContent = 'Coming soon';
      tile.appendChild(badge);
    }

    container.appendChild(tile);
  });
}

async function selectAvatar(voiceName, tileEl) {
  selectedVoice = voiceName;
  document.querySelectorAll('.tutor-tile.selected').forEach((t) => t.classList.remove('selected'));
  if (tileEl) tileEl.classList.add('selected');

  const voiceInfo = voicesData.find((v) => v.name === voiceName);
  avatarVoiceMeta.innerHTML = voiceInfo
    ? `<span>Tone: <strong>${voiceInfo.descriptor}</strong></span><span>Pitch: <strong>${voiceInfo.pitch}</strong></span>`
    : '';

  avatarPreviewHint.textContent = `Loading ${voiceName}...`;
  try {
    await loadAvatarAndPlaySample(voiceName, (pct) => {
      avatarPreviewHint.textContent = `Loading ${voiceName}... ${pct}%`;
    });
    avatarPreviewHint.textContent = voiceName;
    playGreeting();
  } catch (e) {
    avatarPreviewHint.textContent = 'Could not load this avatar - try another.';
    console.error(e);
  }
  updateNextEnabled();
}

targetLanguageInput.addEventListener('input', updateNextEnabled);

backBtn.addEventListener('click', () => {
  // Returning to a profile's detail modal (not just the bare /profiles
  // list) needs the id passed along so that page knows to reopen it -
  // see profilesPage.js's handling of ?open=.
  window.location.href = existingProfileId
    ? `/profiles?open=${encodeURIComponent(existingProfileId)}`
    : '/landing';
});

async function createConversationForProfile(profileId, nativeLanguage, modelName) {
  const convRes = await fetch(`/api/profiles/${profileId}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      voice_name: selectedVoice,
      native_language: nativeLanguage,
      target_language: targetLanguageInput.value.trim(),
      model_name: modelName,
      name: targetLanguageInput.value.trim() || 'Default',
    }),
  });
  if (!convRes.ok) throw new Error('Could not create the conversation.');
}

nextBtn.addEventListener('click', async () => {
  nextBtn.disabled = true;
  backBtn.disabled = true;
  const originalLabel = nextBtn.textContent;

  try {
    if (existingProfileId) {
      // --- Existing profile: just add a new conversation ---
      nextBtn.textContent = 'Starting session...';
      const profileRes = await fetch(`/api/profiles/${existingProfileId}`);
      if (!profileRes.ok) throw new Error('Profile not found.');
      const profile = await profileRes.json();

      await createConversationForProfile(existingProfileId, profile.native_language, profile.model_name);
      // The new conversation is already set as this profile's active one
      // server-side (see create_conversation_endpoint) - this just needed
      // to be set too, or the learning page has no profile to load and
      // bounces straight back to /profiles.
      localStorage.setItem('tutorProfileId', existingProfileId);
      window.location.href = '/';
    } else {
      // --- New profile onboarding, from /landing's draft ---
      const raw = sessionStorage.getItem(DRAFT_KEY);
      if (!raw) { window.location.href = '/landing'; return; }
      const draft = JSON.parse(raw);

      nextBtn.textContent = 'Creating profile...';
      const profileRes = await fetch('/api/profiles', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: draft.name, api_key: draft.api_key }),
      });
      if (!profileRes.ok) throw new Error('Could not create profile.');
      const profile = await profileRes.json();

      await createConversationForProfile(profile.id, draft.native_language, draft.model_name);

      sessionStorage.removeItem(DRAFT_KEY);
      localStorage.setItem('tutorProfileId', profile.id);
      window.location.href = '/';
    }
  } catch (e) {
    console.error(e);
    avatarPreviewHint.textContent = 'Something went wrong - try again.';
    nextBtn.disabled = false;
    backBtn.disabled = false;
    nextBtn.textContent = originalLabel;
  }
});

async function init() {
  if (!existingProfileId && !sessionStorage.getItem(DRAFT_KEY)) {
    window.location.href = '/landing';
    return;
  }

  // Independent per profile/conversation, so it should never carry over
  // from a previous session or the browser's own form-data memory -
  // autocomplete="off" on the input handles most cases, this is the
  // belt-and-suspenders guarantee.
  targetLanguageInput.value = '';

  const [voicesRes, avatarsRes] = await Promise.all([fetch('/api/voices'), fetch('/api/avatars')]);
  const voicesJson = await voicesRes.json();
  const avatarsJson = await avatarsRes.json();
  voicesData = voicesJson.voices;
  availableAvatars = avatarsJson.available;

  renderGrid(femaleGrid, voicesData.filter((v) => v.gender === 'Female'));
  renderGrid(maleGrid, voicesData.filter((v) => v.gender === 'Male'));

  try {
    await initAvatarHead(avatarPreviewEl);
  } catch (e) {
    avatarPreviewHint.textContent = 'Avatar preview could not start - you can still pick a tutor.';
    console.error(e);
  }

  // Nothing pre-selected - the preview stays empty (default hint text)
  // until the user actually clicks a tutor.
  if (!availableAvatars.length) {
    avatarPreviewHint.textContent = 'No avatars ready yet - check back soon.';
  }

  updateNextEnabled();
}

init();
