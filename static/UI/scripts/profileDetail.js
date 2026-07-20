// LanguLearn - profile detail modal (opened from a profile tile on
// /profiles). Shows: microphone (profile-level device choice), and each
// language/conversation as a row with activate/notes/rename/delete
// actions, plus a way to add a new one via avatar-select.

const profileDetailOverlay = document.getElementById('profileDetailOverlay');
const profileDetailName = document.getElementById('profileDetailName');
const profileDetailLanguages = document.getElementById('profileDetailLanguages');
const micSelect = document.getElementById('micSelect');
const refreshMicsBtn = document.getElementById('refreshMicsBtn');
const learnNewLanguageBtn = document.getElementById('learnNewLanguageBtn');
const closeProfileDetailBtn = document.getElementById('closeProfileDetailBtn');

let currentDetailProfile = null;

// --- Microphone (profile-level - reused pattern from the old sidebar
// control, just now scoped to whichever profile's modal is open) ---

async function loadMicsForProfile(profile) {
  try {
    const tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    tempStream.getTracks().forEach((t) => t.stop());
  } catch (e) {
    return; // permission denied - dropdown just stays at "Default microphone"
  }
  const devices = await navigator.mediaDevices.enumerateDevices();
  const mics = devices.filter((d) => d.kind === 'audioinput');
  micSelect.innerHTML = '<option value="">Default microphone</option>';
  mics.forEach((d, i) => {
    const opt = document.createElement('option');
    opt.value = d.deviceId;
    opt.textContent = d.label || `Microphone ${i + 1}`;
    micSelect.appendChild(opt);
  });
  if (profile.mic_device_id && mics.some((d) => d.deviceId === profile.mic_device_id)) {
    micSelect.value = profile.mic_device_id;
  } else if (profile.mic_label) {
    const match = mics.find((d) => d.label === profile.mic_label);
    if (match) micSelect.value = match.deviceId;
  }
}

refreshMicsBtn.addEventListener('click', () => {
  if (currentDetailProfile) loadMicsForProfile(currentDetailProfile);
});

micSelect.addEventListener('change', () => {
  if (!currentDetailProfile) return;
  const selectedOption = micSelect.options[micSelect.selectedIndex];
  const mic_device_id = micSelect.value || null;
  const mic_label = micSelect.value ? selectedOption.textContent : null;
  currentDetailProfile.mic_device_id = mic_device_id;
  currentDetailProfile.mic_label = mic_label;
  fetch(`/api/profiles/${currentDetailProfile.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mic_device_id, mic_label }),
  }).catch(() => {});
});

// --- Languages (conversations) ---

async function fetchConversationsForDetail(profileId) {
  const res = await fetch(`/api/profiles/${profileId}/conversations`);
  if (!res.ok) throw new Error('Failed to load conversations');
  return res.json();
}

async function activateConversation(profileId, conversationId) {
  await fetch(`/api/profiles/${profileId}/active-conversation`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId }),
  });
  localStorage.setItem('tutorProfileId', profileId);
  window.location.href = '/';
}

async function renameConversation(profileId, conversationId, name) {
  await fetch(`/api/profiles/${profileId}/conversations/${conversationId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
}

async function deleteConversation(profileId, conversationId) {
  await fetch(`/api/profiles/${profileId}/conversations/${conversationId}`, { method: 'DELETE' });
}

function renderLanguageRows(profile, conversations) {
  profileDetailLanguages.innerHTML = '';

  if (conversations.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'field-hint';
    empty.style.margin = '0 0 4px';
    empty.textContent = 'No languages yet - add one below.';
    profileDetailLanguages.appendChild(empty);
    return;
  }

  conversations.forEach((conv) => {
    const row = document.createElement('div');
    row.className = 'conv-row';

    const label = conv.name || conv.config?.target_language || 'Language';

    const btn = document.createElement('button');
    btn.className = 'conv-option';
    btn.type = 'button';
    btn.textContent = label;
    btn.title = label;
    btn.addEventListener('click', () => activateConversation(profile.id, conv.id));

    const notesBtn = document.createElement('button');
    notesBtn.className = 'conv-notes-btn';
    notesBtn.type = 'button';
    notesBtn.title = 'View notes for this language';
    notesBtn.textContent = '📝';
    notesBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      openNotesModal(profile.id, conv.id, label);
    });

    const renameBtn = document.createElement('button');
    renameBtn.className = 'conv-rename-btn';
    renameBtn.type = 'button';
    renameBtn.title = 'Rename';
    renameBtn.textContent = '\u270E';
    renameBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const name = prompt('Rename language', label);
      if (name === null) return;
      const trimmed = name.trim();
      if (!trimmed) return;
      await renameConversation(profile.id, conv.id, trimmed);
      await refreshLanguageRows(profile);
    });

    const delBtn = document.createElement('button');
    delBtn.className = 'conv-delete-btn';
    delBtn.type = 'button';
    delBtn.title = 'Delete';
    delBtn.textContent = '×';
    delBtn.addEventListener('click', async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete "${label}"? This cannot be undone.`)) return;
      await deleteConversation(profile.id, conv.id);
      await refreshLanguageRows(profile);
    });

    row.appendChild(btn);
    row.appendChild(notesBtn);
    row.appendChild(renameBtn);
    row.appendChild(delBtn);
    profileDetailLanguages.appendChild(row);
  });
}

async function refreshLanguageRows(profile) {
  const data = await fetchConversationsForDetail(profile.id);
  renderLanguageRows(profile, data.conversations || []);
}

// --- Modal open/close ---

async function openProfileDetail(profile) {
  currentDetailProfile = profile;
  profileDetailName.textContent = profile.name;
  profileDetailOverlay.classList.add('visible');

  await loadMicsForProfile(profile);
  await refreshLanguageRows(profile);
}

function closeProfileDetail() {
  profileDetailOverlay.classList.remove('visible');
  currentDetailProfile = null;
}

closeProfileDetailBtn.addEventListener('click', closeProfileDetail);
profileDetailOverlay.addEventListener('click', (e) => {
  if (e.target === profileDetailOverlay) closeProfileDetail();
});

learnNewLanguageBtn.addEventListener('click', () => {
  if (!currentDetailProfile) return;
  window.location.href = `/avatar-select?profile_id=${encodeURIComponent(currentDetailProfile.id)}`;
});
