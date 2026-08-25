// LanguLearn - /profiles picker. Netflix-style tiles: click a profile to
// open its detail modal (mic + languages, see profileDetail.js), add a
// new one (hands off to /get-started), or import one from a backup zip
// (see the Import profile section below).

const profileTiles = document.getElementById('profileTiles');
const ACTIVE_PROFILE_KEY = 'tutorProfileId';

async function fetchProfiles() {
  const res = await fetch('/api/profiles');
  const data = await res.json();
  return data.profiles;
}

async function fetchFullProfile(id) {
  const res = await fetch(`/api/profiles/${id}`);
  if (!res.ok) throw new Error('Profile not found');
  return res.json();
}

async function deleteProfileApi(id) {
  await fetch(`/api/profiles/${id}`, { method: 'DELETE' });
}

function buildProfileTile(profile, activeId) {
  const tile = document.createElement('div');
  tile.className = 'profile-tile' + (profile.id === activeId ? ' active' : '');

  const selectBtn = document.createElement('button');
  selectBtn.type = 'button';
  selectBtn.className = 'profile-tile-select';
  selectBtn.title = profile.name;

  const avatar = document.createElement('div');
  avatar.className = 'profile-tile-avatar';
  avatar.textContent = (profile.name[0] || '?').toUpperCase();

  const name = document.createElement('span');
  name.className = 'profile-tile-name';
  name.textContent = profile.name;

  selectBtn.appendChild(avatar);
  selectBtn.appendChild(name);
  // The list endpoint only returns {id, name} - the detail modal needs the
  // full record (api_key, mic_device_id, etc.), so fetch that on click
  // rather than passing the shallow list entry straight through.
  selectBtn.addEventListener('click', async () => {
    const fullProfile = await fetchFullProfile(profile.id);
    openProfileDetail(fullProfile);
  });

  const delBtn = document.createElement('button');
  delBtn.type = 'button';
  delBtn.className = 'profile-tile-delete';
  delBtn.title = 'Delete profile';
  delBtn.textContent = '×';
  delBtn.addEventListener('click', async (e) => {
    e.stopPropagation();
    if (!confirm(`Delete profile "${profile.name}"? This cannot be undone.`)) return;
    await deleteProfileApi(profile.id);
    if (localStorage.getItem(ACTIVE_PROFILE_KEY) === profile.id) {
      localStorage.removeItem(ACTIVE_PROFILE_KEY);
      // The Settings modal's own delete-profile flow (settings.js) gets
      // this for free by navigating to /profiles afterward, which
      // reruns profileMenu.js's initProfileMenu() from scratch. This
      // button deletes without leaving the page, so nothing else would
      // otherwise clear the top bar's in-memory currentProfile/dropdown -
      // it'd keep showing the just-deleted profile as logged in, and
      // Settings would still open for it (backed by a 404'ing profile_id)
      // until the next full navigation.
      if (currentProfile && currentProfile.id === profile.id) {
        currentProfile = null;
        refreshProfileMenuButton();
      }
    }
    render();
  });

  tile.appendChild(selectBtn);
  tile.appendChild(delBtn);
  return tile;
}

function buildAddTile() {
  const tile = document.createElement('div');
  tile.className = 'profile-tile profile-tile-add';

  const addBtn = document.createElement('button');
  addBtn.type = 'button';
  addBtn.className = 'profile-tile-select';

  const avatar = document.createElement('div');
  avatar.className = 'profile-tile-avatar';
  avatar.textContent = '+';

  const name = document.createElement('span');
  name.className = 'profile-tile-name';
  name.textContent = 'Add profile';

  addBtn.appendChild(avatar);
  addBtn.appendChild(name);
  addBtn.addEventListener('click', () => { window.location.href = '/get-started'; });

  tile.appendChild(addBtn);
  return tile;
}

// --- Import profile (restore a backup zip - see backup.py) ---
// Lives here rather than only in the Settings modal's Data controls tab
// (settings.js) because that modal requires an active profile to open -
// exactly the case a fresh install or a wiped-profiles machine doesn't
// have. This tile is the only way back in for that case: pick the backup
// zip straight from the profile picker, no profile needed first.

async function importProfileApi(file) {
  const formData = new FormData();
  formData.append('file', file);
  const res = await fetch('/api/profiles/import', { method: 'POST', body: formData });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

// One shared hidden file input for the tile's button to proxy clicks to -
// same pattern as settings.js's settingsImportProfileFile.
const profileImportFileInput = document.createElement('input');
profileImportFileInput.type = 'file';
profileImportFileInput.accept = '.zip';
profileImportFileInput.hidden = true;
document.body.appendChild(profileImportFileInput);

profileImportFileInput.addEventListener('change', async () => {
  const file = profileImportFileInput.files[0];
  profileImportFileInput.value = ''; // reset - re-selecting the same file should still fire 'change' next time
  if (!file) return;
  try {
    await importProfileApi(file);
    // Re-fetches and rebuilds the tile grid in place so the imported
    // profile actually appears - a plain success message wouldn't be
    // enough, since this page's tile list was already fetched once
    // before this file existed.
    await render();
  } catch (e) {
    alert("Could not import that file - make sure it's a LanguLearn backup zip.");
  }
});

function buildImportTile() {
  const tile = document.createElement('div');
  tile.className = 'profile-tile profile-tile-add';

  const importBtn = document.createElement('button');
  importBtn.type = 'button';
  importBtn.className = 'profile-tile-select';

  const avatar = document.createElement('div');
  avatar.className = 'profile-tile-avatar';
  avatar.textContent = '+';

  const name = document.createElement('span');
  name.className = 'profile-tile-name';
  name.textContent = 'Import profile';

  importBtn.appendChild(avatar);
  importBtn.appendChild(name);
  importBtn.addEventListener('click', () => profileImportFileInput.click());

  tile.appendChild(importBtn);
  return tile;
}

function buildOrDivider() {
  const or = document.createElement('span');
  or.className = 'profile-tile-or';
  or.textContent = 'or';
  return or;
}

async function render() {
  const profiles = await fetchProfiles();
  const activeId = localStorage.getItem(ACTIVE_PROFILE_KEY);

  profileTiles.innerHTML = '';
  profiles.forEach((p) => profileTiles.appendChild(buildProfileTile(p, activeId)));
  profileTiles.appendChild(buildAddTile());
  profileTiles.appendChild(buildOrDivider());
  profileTiles.appendChild(buildImportTile());

  const existingHint = document.getElementById('profilesEmptyHint');
  if (existingHint) existingHint.remove();
  if (profiles.length === 0) {
    const hint = document.createElement('p');
    hint.id = 'profilesEmptyHint';
    hint.textContent = "No profiles yet - add one, or import a backup, to get started.";
    profileTiles.after(hint);
  }

  // Returning from avatar-select's Back button (via "+ Learn a new
  // language" on a specific profile) should land back in that same
  // profile's detail modal, not just the bare tile grid.
  const openId = new URLSearchParams(window.location.search).get('open');
  if (openId && profiles.some((p) => p.id === openId)) {
    const fullProfile = await fetchFullProfile(openId);
    openProfileDetail(fullProfile);
  }
}

render();
