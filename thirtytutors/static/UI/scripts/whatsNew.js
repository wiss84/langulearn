const whatsNewVersionSelect = document.getElementById('whatsNewVersionSelect');
if (whatsNewVersionSelect) {
  whatsNewVersionSelect.addEventListener('change', () => {
    window.location.href = `/whats-new?version=${encodeURIComponent(whatsNewVersionSelect.value)}`;
  });
}

if (!sessionStorage.getItem('whats_new_return_url')) {
  const ref = document.referrer;
  if (ref && !ref.endsWith('/whats-new') && !ref.includes('/whats-new?')) {
    sessionStorage.setItem('whats_new_return_url', ref);
  }
}

const whatsNewCloseBtn = document.getElementById('whatsNewCloseBtn');
if (whatsNewCloseBtn) {
  whatsNewCloseBtn.addEventListener('click', () => {
    const returnUrl = sessionStorage.getItem('whats_new_return_url') || '/profiles';
    window.location.href = returnUrl;
  });
}
