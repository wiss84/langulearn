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
      // Fire-and-forget: warms up the mic (getUserMedia + AudioContext +
      // worklet graph - see ensureMicReady in audio.js) as soon as we know
      // there's a real profile/conversation to talk in, rather than paying
      // that one-time cost (4-7s on Windows built-in mics - see audio.js's
      // notes) on the user's first press of the talk button. Errors are
      // swallowed here on purpose - if this fails silently (e.g. permission
      // denied), the user's first real press re-discovers it through
      // startRecording's own error handling, which resets micReadyPromise
      // and shows a proper error, so nothing gets silently stuck.
      ensureMicReady().catch(() => {});
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
