// LanguLearn - current-profile bootstrap for the learning page.
//
// Everything related to switching profiles, API key, switching/adding
// languages, microphone selection, and viewing notes now lives on the
// dedicated /profiles page (see profilesPage.js and profileDetail.js) or
// /landing (API key, collected once at profile creation) - reachable via
// the Home link in the top bar. This file only ever loads whichever
// profile/conversation was already chosen there.

async function fetchProfile(id) {
  const res = await fetch(`/api/profiles/${id}`);
  if (!res.ok) throw new Error('Profile not found');
  return res.json();
}

async function applyProfile(profile) {
  currentProfile = profile;
  currentConversationId = null;
  // settings.js defines this once its topbar button exists - guarded since
  // profiles.js has no direct dependency on settings.js loading at all.
  if (typeof refreshSettingsAvatar === 'function') refreshSettingsAvatar();
  await loadConversationsForProfile();
}
