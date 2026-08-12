// Update notification bell (top bar, base template - see index.html) +
// the learning page's profile dropdown persistent item (if present on
// this page - see settings.js's #settingsDropdown) + the Settings modal's
// Updates tab (also settings.js). All three read the same
// /api/update-status endpoint and share the action logic here, so
// "Update & Relaunch" behaves identically no matter which entry point
// triggered it. Loaded unconditionally in index.html (like theme.js), so
// it runs on every page, not just the learning page - see the design
// discussion this replaced (a badge tucked inside the learning-page-only
// profile dropdown would never be seen from the /profiles launch screen).
//
// Plain global functions/vars, not a module - same reasoning as every
// other <script src> pair on this page (see settings.js's own header
// comment): they all share one global lexical scope, and settings.js
// (loaded later on the learning page) calls straight into loadUpdateStatus/
// runUpdateAction/describeUpdate below for its Updates tab.
//
// sessionStorage (not localStorage) for the "skipped" flag: it needs to
// persist across this app's own page-to-page navigation (a real page
// reload every time - /profiles, /learning, etc. are separate server-
// rendered routes, not an SPA) for as long as this one launch's webview
// window stays open, but must NOT survive to the next app launch - a new
// pywebview window is a genuinely new browsing session, so sessionStorage
// resets there on its own without any extra code.

const updateBellBtn = document.getElementById('updateBellBtn');
const updateBellDot = document.getElementById('updateBellDot');
const updateBellDropdown = document.getElementById('updateBellDropdown');
const updateNotificationList = document.getElementById('updateNotificationList');
const updateBellEmptyText = document.getElementById('updateBellEmptyText');

const updateDetailOverlay = document.getElementById('updateDetailOverlay');
const updateDetailTitle = document.getElementById('updateDetailTitle');
const updateDetailMessage = document.getElementById('updateDetailMessage');
const updateDetailActionBtn = document.getElementById('updateDetailActionBtn');
const updateDetailSkipBtn = document.getElementById('updateDetailSkipBtn');
const updateDetailStatus = document.getElementById('updateDetailStatus');
const closeUpdateDetailBtn = document.getElementById('closeUpdateDetailBtn');

let latestUpdateStatus = null; // {app: {current, latest, update_available}, assets: {current, latest, update_available}}
let latestWhatsNewStatus = null; // {available, version}

function updateIsSkipped() {
  return sessionStorage.getItem('langulearn_update_skipped') === '1';
}

// Single source of truth for "what does this update mean and what should
// the button say" - app-code updates need a relaunch (loaded Python
// modules won't pick up new files until the process restarts), asset
// updates don't (StaticFiles serves them straight off disk - see main.py -
// so a fresh page load already has them).
function describeUpdate(status) {
  if (!status) return null;
  const appUp = status.app && status.app.update_available;
  const assetsUp = status.assets && status.assets.update_available;
  if (appUp && assetsUp) {
    return {
      message: `A new version of LanguLearn is available (v${status.app.current} \u2192 v${status.app.latest}), along with updated avatars/voices/photos.`,
      actionLabel: 'Update & Relaunch',
      needsRelaunch: true,
    };
  }
  if (appUp) {
    return {
      message: `A new version of LanguLearn is available: v${status.app.current} \u2192 v${status.app.latest}.`,
      actionLabel: 'Update & Relaunch',
      needsRelaunch: true,
    };
  }
  if (assetsUp) {
    return {
      message: 'New avatars, voices, or photos are available to download.',
      actionLabel: 'Update Assets',
      needsRelaunch: false,
    };
  }
  return null;
}

// setStatus: (text) => void - each entry point (bell, profile dropdown
// item, Settings tab) passes its own place to show progress text.
async function runUpdateAction(setStatus, actionBtn) {
  const info = describeUpdate(latestUpdateStatus);
  if (!info) return;
  actionBtn.disabled = true;
  try {
    if (latestUpdateStatus.app.update_available) {
      setStatus('Updating LanguLearn...');
      const res = await fetch('/api/update-app', { method: 'POST' });
      if (!res.ok) throw new Error(await res.text());
    }
    if (latestUpdateStatus.assets.update_available) {
      setStatus('Downloading updated assets...');
      const res = await fetch('/api/update-assets', { method: 'POST' });
      if (!res.ok) throw new Error(await res.text());
    }
    if (info.needsRelaunch) {
      setStatus('Restarting...');
      // Doesn't await the response body meaningfully - the window closes
      // itself shortly after the server sends this, so there's nothing
      // further to react to here even on success.
      fetch('/api/restart-app', { method: 'POST' }).catch(() => {});
    } else {
      setStatus('Assets updated - ready to use.');
      latestUpdateStatus.assets.update_available = false;
      refreshUpdateUI();
      actionBtn.disabled = false;
    }
  } catch (e) {
    setStatus('Update failed - check your connection and try again.');
    actionBtn.disabled = false;
  }
}

// One entry per pending notification. Each kind owns its own dismiss rule:
// an update stays pending as long as it's genuinely still available (skip
// only quiets the badge for this session, per updateIsSkipped above) -
// what's-new has nothing to "resolve", so visiting its page is what clears
// it (see routes_pages.py's /whats-new, which marks the version seen on
// every request).
function buildNotifications() {
  const notifications = [];

  const updateInfo = describeUpdate(latestUpdateStatus);
  if (updateInfo) {
    notifications.push({
      kind: 'update',
      message: updateInfo.message,
      countsTowardBadge: !updateIsSkipped(),
      onClick: () => {
        closeBellDropdown();
        openUpdateDetail();
      },
    });
  }

  if (latestWhatsNewStatus && latestWhatsNewStatus.available) {
    notifications.push({
      kind: 'whats_new',
      message: `See what's new in v${latestWhatsNewStatus.version}.`,
      countsTowardBadge: true,
      onClick: () => {
        window.location.href = '/whats-new';
      },
    });
  }

  return notifications;
}

function closeBellDropdown() {
  updateBellDropdown.classList.remove('visible');
}

function openUpdateDetail() {
  const info = describeUpdate(latestUpdateStatus);
  if (!info) return;
  updateDetailTitle.textContent = 'Update available';
  updateDetailMessage.textContent = info.message;
  updateDetailActionBtn.textContent = info.actionLabel;
  updateDetailActionBtn.disabled = false;
  updateDetailStatus.textContent = '';
  updateDetailOverlay.classList.add('visible');
}

function closeUpdateDetail() {
  updateDetailOverlay.classList.remove('visible');
}

// Rebuilds every surface's visibility/labels from the current state -
// called after every fetch and after any state change (skip, a
// successful assets-only update that doesn't need a relaunch, returning
// from the what's-new page).
//
// The bell itself is ALWAYS visible (a fixed, always-clickable part of
// the top bar - a notification icon that sometimes just isn't there
// isn't recognizable as "check here"). Only its badge count and dropdown
// content change.
//
// The bell dropdown is a plain LIST - one row per pending notification,
// no action buttons in it. Clicking a row runs that notification's own
// onClick (see buildNotifications above) - an update opens the detail
// modal, where "Update & Relaunch"/"Skip for now" actually live; what's-
// new navigates straight to its page.
function refreshUpdateUI() {
  const notifications = buildNotifications();
  const badgeCount = notifications.filter((n) => n.countsTowardBadge).length;

  updateBellDot.hidden = badgeCount === 0;
  updateBellDot.textContent = badgeCount > 9 ? '9+' : String(badgeCount);

  updateNotificationList.innerHTML = '';
  notifications.forEach((n) => {
    const row = document.createElement('button');
    row.type = 'button';
    row.className = 'update-notification-item';
    row.textContent = n.message;
    row.addEventListener('click', n.onClick);
    updateNotificationList.appendChild(row);
  });
  updateBellEmptyText.hidden = notifications.length > 0;

  // Learning page's profile dropdown (settings.js) - only exists on that
  // page. Shown there whenever an update is available, regardless of
  // skip - this is the deliberately-always-reachable entry point once a
  // person has been shown the bell at least once. What's-new doesn't get
  // an entry here - it's a one-time thing, reachable only through the
  // bell and, permanently, through Settings -> About.
  const info = describeUpdate(latestUpdateStatus);
  const hasUpdate = !!info;
  const settingsDropdown = document.getElementById('settingsDropdown');
  if (settingsDropdown) {
    let item = document.getElementById('updateDropdownItem');
    if (hasUpdate && !item) {
      item = document.createElement('button');
      item.id = 'updateDropdownItem';
      item.className = 'settings-dropdown-item';
      item.type = 'button';
      item.addEventListener('click', () => {
        const original = item.textContent;
        runUpdateAction((text) => { item.textContent = text; }, item).then(() => {
          if (!item.disabled) item.textContent = original; // restore label after a non-relaunch success/failure
        });
      });
      settingsDropdown.insertBefore(item, settingsDropdown.firstChild);
    }
    if (item) {
      item.hidden = !hasUpdate;
      if (info) item.textContent = '\ud83d\udd04 ' + info.actionLabel;
    }
  }
}

async function loadUpdateStatus(force) {
  try {
    const res = await fetch(`/api/update-status${force ? '?force=true' : ''}`);
    latestUpdateStatus = await res.json();
  } catch (e) {
    latestUpdateStatus = null;
  }
  refreshUpdateUI();
  return latestUpdateStatus;
}

async function loadWhatsNewStatus() {
  try {
    const res = await fetch('/api/whats-new-status');
    latestWhatsNewStatus = await res.json();
  } catch (e) {
    latestWhatsNewStatus = null;
  }
  refreshUpdateUI();
  return latestWhatsNewStatus;
}

updateBellBtn.addEventListener('click', (e) => {
  e.stopPropagation();
  updateBellDropdown.classList.toggle('visible');
});
document.addEventListener('click', (e) => {
  if (!updateBellDropdown.contains(e.target) && e.target !== updateBellBtn) closeBellDropdown();
});

closeUpdateDetailBtn.addEventListener('click', closeUpdateDetail);
updateDetailOverlay.addEventListener('click', (e) => {
  if (e.target === updateDetailOverlay) closeUpdateDetail();
});
updateDetailSkipBtn.addEventListener('click', () => {
  sessionStorage.setItem('langulearn_update_skipped', '1');
  closeUpdateDetail();
  refreshUpdateUI();
});
updateDetailActionBtn.addEventListener('click', () => {
  runUpdateAction((text) => { updateDetailStatus.textContent = text; }, updateDetailActionBtn);
});

loadUpdateStatus(false);
loadWhatsNewStatus();
