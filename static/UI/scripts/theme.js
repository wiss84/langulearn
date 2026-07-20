// LanguLearn - light/dark theme toggle. Loaded once, right after the
// shared top bar in index.html, so it runs on every page. Detection (so
// the right theme applies before first paint) happens separately, inline
// in index.html's <head> - this only wires up the click-to-toggle
// behavior once #themeToggleBtn exists.
(function () {
  const btn = document.getElementById('themeToggleBtn');
  if (!btn) return;

  function updateIcon() {
    const isLight = document.documentElement.getAttribute('data-theme') === 'light';
    btn.textContent = isLight ? '🌙' : '☀️';
    btn.title = isLight ? 'Switch to dark theme' : 'Switch to light theme';
  }

  btn.addEventListener('click', () => {
    const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('theme', next);
    updateIcon();
  });

  updateIcon();
})();
