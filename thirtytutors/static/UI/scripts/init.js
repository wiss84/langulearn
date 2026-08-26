// ThirtyTutors - app bootstrap. Must load last: everything it calls
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
      // Fire-and-forget - see backup.maybe_run_auto_backup's own docstring
      // for what this actually does (a no-op unless auto-backup is on AND
      // its interval has elapsed). Once per learning-page load is the
      // "once per app session" cadence this is meant to run at - no need
      // to await or surface anything here either way.
      fetch(`/api/profiles/${savedId}/check-auto-backup`, { method: 'POST' }).catch(() => {});
      return;
    } catch (e) {
      localStorage.removeItem('tutorProfileId');
    }
  }
  // No active profile (first run, or a stale/deleted one) - /landing is
  // the app's universal entry point (see desktop.py), so bounce there
  // rather than assuming this page should render without one.
  window.location.href = '/landing';
}

init();
