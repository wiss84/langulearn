// LanguLearn - light/dark theme toggle. Loaded once, right after the
// shared top bar in index.html, so it runs on every page. Detection (so
// the right theme applies before first paint) happens separately, inline
// in index.html's <head> - this only wires up the click-to-toggle
// behavior once #themeToggleBtn exists (it doesn't on the learning page,
// which suppresses the shared button via index.html's theme_toggle block
// and instead offers the toggle from inside the Settings modal - see
// settings.js's settingsThemeBtn, which calls toggleAppTheme() below
// directly since there's no #themeToggleBtn there to click on its behalf).

function updateThemeToggleIcon() {
  const btn = document.getElementById('themeToggleBtn');
  if (!btn) return;
  const isLight = document.documentElement.getAttribute('data-theme') === 'light';
  btn.textContent = isLight ? '🌑' : '☀️';
  btn.title = isLight ? 'Switch to dark theme' : 'Switch to light theme';
}

function toggleAppTheme() {
  const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem('theme', next);
  updateThemeToggleIcon();
}

(function () {
  const btn = document.getElementById('themeToggleBtn');
  if (btn) btn.addEventListener('click', toggleAppTheme);
  updateThemeToggleIcon();
})();
