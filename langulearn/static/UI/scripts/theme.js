// LanguLearn - theme (System / Light / Dark). Loaded once, globally, in
// index.html right after the shared top bar. Detection (so the right
// theme applies before first paint) happens separately, inline in
// index.html's <head> - this only wires up the actual mode switch,
// called from the Settings modal's General tab (settings.js's segmented
// control - there is no standalone top-bar toggle button anymore, see
// index.html's own comment for why).
//
// localStorage.theme holds the MODE the person picked: 'system' | 'light'
// | 'dark'. data-theme on <html> holds that SAME value directly - 'system'
// is its own distinct value, not resolved down to 'light'/'dark' in JS.
// base.css keys its colors off data-theme, and 'system' has its own CSS
// block there that follows the OS preference live via @media
// (prefers-color-scheme), so no JS listener is needed to react to the OS
// theme changing while the app is open - the browser repaints it on its
// own.

function resolveTheme(mode) {
  return mode === 'system' ? 'system' : mode;
}

function currentThemeMode() {
  const saved = localStorage.getItem('theme');
  return saved === 'light' || saved === 'dark' || saved === 'system' ? saved : 'light';
}

// Applies a mode (persisting it) and repaints. Exported for
// settings.js's segmented control to call directly.
function setThemeMode(mode) {
  localStorage.setItem('theme', mode);
  document.documentElement.setAttribute('data-theme', resolveTheme(mode));
  if (typeof refreshThemeSegmentedControl === 'function') refreshThemeSegmentedControl();
}

