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
  let tempStream;
  try {
    tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (e) {
    return; // permission denied - dropdown just stays at "Default microphone"
  }
  // Requesting audio:true with no deviceId constraint resolves to whatever
  // the OS currently treats as the default input - reading it back off the
  // resulting track (before we stop it) is how we later resolve "Default
  // microphone" to a concrete device instead of leaving it ambiguous. See
  // resolveAndPinDefaultMic below for why this matters.
  const resolvedDefaultDeviceId = tempStream.getAudioTracks()[0]?.getSettings().deviceId || null;
  tempStream.getTracks().forEach((t) => t.stop());

  const devices = await navigator.mediaDevices.enumerateDevices();
  const mics = dedupeMicsByGroup(devices.filter((d) => d.kind === 'audioinput'));
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

  if (!profile.mic_device_id) {
    await resolveAndPinDefaultMic(profile, mics, resolvedDefaultDeviceId);
  }
}

// "Default microphone" is convenient in the dropdown, but ambiguous as a
// stored identity: the app has no way to know what it silently maps to
// without asking the OS, and if left unresolved, calibration/enrollment
// data would get keyed to a generic placeholder that could secretly mean a
// different physical device on a different day (if the person changes
// their OS default mic outside the app). So the first time a profile is
// used with no explicit mic chosen yet, this resolves "Default" to whatever
// concrete device it points to right now and pins that as the profile's
// actual selection - the dropdown then shows the real device name instead
// of "Default microphone", which is deliberate: it's telling the person
// exactly which physical mic they're actually about to use, removing the
// ambiguity rather than hiding it. If the OS default later changes, picking
// "Default microphone" again re-resolves and re-pins to the new device.
async function resolveAndPinDefaultMic(profile, mics, resolvedDefaultDeviceId) {
  if (!resolvedDefaultDeviceId) return;
  const resolved = mics.find((d) => d.deviceId === resolvedDefaultDeviceId);
  if (!resolved) return;

  profile.mic_device_id = resolved.deviceId;
  profile.mic_label = resolved.label;
  micSelect.value = resolved.deviceId;
  await fetch(`/api/profiles/${profile.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mic_device_id: resolved.deviceId, mic_label: resolved.label }),
  }).catch(() => {});
}

// Windows exposes the same physical mic multiple times - once as the
// device itself, and again for each "role" (Default / Communications) it's
// been assigned to in Windows sound settings. Chrome surfaces all of these
// as separate deviceIds, but tags role-duplicates of the same physical
// hardware with a shared groupId - so collapsing to one entry per groupId
// removes the duplicates. Prefers the plain device label (no "Default -"/
// "Communications -" prefix) when one exists, since that's the more
// meaningful name to show.
function dedupeMicsByGroup(mics) {
  const byGroup = new Map();
  for (const d of mics) {
    const key = d.groupId || d.deviceId;
    const existing = byGroup.get(key);
    const isPlain = !/^(Default|Communications) -/.test(d.label || '');
    if (!existing || (isPlain && /^(Default|Communications) -/.test(existing.label || ''))) {
      byGroup.set(key, d);
    }
  }
  return Array.from(byGroup.values());
}

refreshMicsBtn.addEventListener('click', () => {
  if (currentDetailProfile) loadMicsForProfile(currentDetailProfile);
});

micSelect.addEventListener('change', async () => {
  if (!currentDetailProfile) return;
  if (!micSelect.value) {
    // They explicitly picked "Default microphone" again - resolve it to a
    // concrete device right away rather than storing the ambiguous null
    // (see resolveAndPinDefaultMic's comment).
    const devices = await navigator.mediaDevices.enumerateDevices();
    const mics = dedupeMicsByGroup(devices.filter((d) => d.kind === 'audioinput'));
    let tempStream;
    try {
      tempStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      return;
    }
    const resolvedDefaultDeviceId = tempStream.getAudioTracks()[0]?.getSettings().deviceId || null;
    tempStream.getTracks().forEach((t) => t.stop());
    await resolveAndPinDefaultMic(currentDetailProfile, mics, resolvedDefaultDeviceId);
    return;
  }
  const selectedOption = micSelect.options[micSelect.selectedIndex];
  const mic_device_id = micSelect.value;
  const mic_label = selectedOption.textContent;
  currentDetailProfile.mic_device_id = mic_device_id;
  currentDetailProfile.mic_label = mic_label;
  fetch(`/api/profiles/${currentDetailProfile.id}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mic_device_id, mic_label }),
  }).catch(() => {});
});

// --- Mic calibration ---
// Measures ~2s of ambient room noise and derives a silence threshold from
// it (noise floor RMS * a safety margin). Stored per-mic in
// profile.mic_calibrations (keyed by mic label, "__default__" sentinel for
// the OS default - same key handsfreeSetup.js and live_session.py use) so
// switching mics never overwrites a different mic's calibration. Same idea
// as the ambient-noise calibration in the noise_recorder project: stay
// quiet, measure, derive a number from it - just a linear RMS margin
// instead of a dBFS offset, since that's what the hands-free gate compares
// against directly.
const calibrateMicBtn = document.getElementById('calibrateMicBtn');
const calibrateMicStatus = document.getElementById('calibrateMicStatus');
const CALIBRATION_SECONDS = 2;
const CALIBRATION_MARGIN = 3; // ambient noise floor * this = the silence threshold
const MIC_SETTLE_TIMEOUT_MS = 5000; // safety cap - see waitForFirstAudioFrame
const DEFAULT_MIC_CALIBRATION_KEY = '__default__';

function micCalibrationKey(profile) {
  return profile.mic_label || DEFAULT_MIC_CALIBRATION_KEY;
}

// A mic that hasn't been opened yet this page session can take anywhere
// from a few hundred ms to several seconds to actually start delivering
// audio frames on Windows (hardware negotiation) - a fixed delay either
// wastes time or, as seen in testing (500ms wasn't enough on one real
// machine), still isn't long enough and captures zero samples, which
// divides-by-zero into a NaN threshold. Polling for the first real frame
// instead adapts to however long this specific mic/machine actually needs,
// with a safety cap so a genuinely dead mic doesn't hang forever.
function waitForFirstAudioFrame(samplesRef) {
  return new Promise((resolve) => {
    let done = false;
    const finish = () => { if (!done) { done = true; clearInterval(poll); resolve(); } };
    const poll = setInterval(() => { if (samplesRef.length > 0) finish(); }, 50);
    setTimeout(finish, MIC_SETTLE_TIMEOUT_MS);
  });
}

async function calibrateMic(profile) {
  calibrateMicBtn.disabled = true;
  calibrateMicStatus.textContent = 'Stay quiet for a couple seconds...';

  let context, stream, workletNode;
  let samples = [];
  try {
    const constraints = profile.mic_device_id ? { audio: { deviceId: { exact: profile.mic_device_id } } } : { audio: true };
    stream = await navigator.mediaDevices.getUserMedia(constraints);
    context = new AudioContext({ sampleRate: 16000 });
    await context.audioWorklet.addModule('/pcm-processor.js');
    const source = context.createMediaStreamSource(stream);
    workletNode = new AudioWorkletNode(context, 'pcm-capture-processor');
    workletNode.port.onmessage = (event) => samples.push(event.data);
    source.connect(workletNode);

    await waitForFirstAudioFrame(samples);
    samples = [];
    workletNode.port.onmessage = (event) => samples.push(event.data);

    await new Promise((r) => setTimeout(r, CALIBRATION_SECONDS * 1000));

    stream.getTracks().forEach((t) => t.stop());
    workletNode.port.onmessage = null;
    context.close();

    let total = 0;
    samples.forEach((s) => { total += s.length; });
    if (total === 0) {
      calibrateMicStatus.textContent = "This mic isn't delivering audio - check it's not muted/disabled, then try again.";
      return;
    }
    const combined = new Float32Array(total);
    let offset = 0;
    samples.forEach((s) => { combined.set(s, offset); offset += s.length; });

    let sumSquares = 0;
    for (let i = 0; i < combined.length; i++) sumSquares += combined[i] * combined[i];
    const noiseFloorRms = Math.sqrt(sumSquares / combined.length);
    const threshold = noiseFloorRms * CALIBRATION_MARGIN;

    const key = micCalibrationKey(profile);
    const calibrations = { ...(profile.mic_calibrations || {}) };
    calibrations[key] = { ...(calibrations[key] || {}), silence_rms_threshold: threshold, calibrated: true };

    await fetch(`/api/profiles/${profile.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mic_calibrations: calibrations }),
    });
    currentDetailProfile.mic_calibrations = calibrations;
    calibrateMicStatus.textContent = `Calibrated (threshold ${threshold.toFixed(4)}) - takes effect on your next hands-free session.`;
  } catch (e) {
    console.error(e);
    calibrateMicStatus.textContent = 'Calibration failed - check microphone access and try again.';
  } finally {
    calibrateMicBtn.disabled = false;
  }
}

calibrateMicBtn.addEventListener('click', () => {
  if (currentDetailProfile) calibrateMic(currentDetailProfile);
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
