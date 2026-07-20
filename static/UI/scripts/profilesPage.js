// LanguLearn - /profiles picker (the "Home" destination, and where a
// fresh app load lands when there's no active profile yet). Netflix-style
// tiles: click a profile to open its detail modal (mic + languages, see
// profileDetail.js), or add a new one (hands off to /landing, same as
// everywhere else a new profile gets created).

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
  addBtn.addEventListener('click', () => { window.location.href = '/landing'; });

  tile.appendChild(addBtn);
  return tile;
}

async function render() {
  const profiles = await fetchProfiles();
  const activeId = localStorage.getItem(ACTIVE_PROFILE_KEY);

  profileTiles.innerHTML = '';
  profiles.forEach((p) => profileTiles.appendChild(buildProfileTile(p, activeId)));
  profileTiles.appendChild(buildAddTile());

  const existingHint = document.getElementById('profilesEmptyHint');
  if (existingHint) existingHint.remove();
  if (profiles.length === 0) {
    const hint = document.createElement('p');
    hint.id = 'profilesEmptyHint';
    hint.textContent = "No profiles yet - add one to get started.";
    profileTiles.after(hint);
  }
}

render();
