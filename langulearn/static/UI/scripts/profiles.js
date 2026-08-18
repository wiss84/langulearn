// LanguLearn - current-profile bootstrap for the learning page.
//
// Everything related to switching profiles, API key, switching/adding
// languages, microphone selection, and viewing notes now lives on the
// dedicated /profiles page (see profilesPage.js and profileDetail.js) or
// /get-started (API key, collected once at profile creation) - reachable
// via the profile-switcher icon in the top bar. This file only ever loads
// whichever profile/conversation was already chosen there.
//
// fetchProfile() is declared in profileMenu.js (loaded globally, before
// this file) - reused here as-is rather than redeclared.

async function applyProfile(profile) {
  currentProfile = profile;
  currentConversationId = null;
  // settings.js/profileMenu.js define this once the top-bar button exists
  // - guarded since profiles.js has no direct dependency on either
  // loading at all.
  if (typeof refreshProfileMenuButton === 'function') refreshProfileMenuButton();
  await loadConversationsForProfile();
}
