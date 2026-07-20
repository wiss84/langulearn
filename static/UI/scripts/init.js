// LanguLearn - app bootstrap. Must load last: everything it calls
// (resizeCanvas/drawWaveform from audio.js, fetchProfile/applyProfile
// from profiles.js) needs to already be defined.

async function init() {
  resizeCanvas();
  requestAnimationFrame(drawWaveform);

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
  // No active profile (first run, or a stale/deleted one) - the /profiles
  // picker is the gate for entering the app, same idea as a Netflix-style
  // "who's watching" screen. It links to /landing itself if there are no
  // profiles at all yet.
  window.location.href = '/profiles';
}

init();
