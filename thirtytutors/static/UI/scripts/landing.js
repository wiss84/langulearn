// ThirtyTutors - marketing/home page (/landing). The app's universal entry
// point on every launch (see desktop.py) - the CTA buttons adapt to
// whether a profile is already active (localStorage.tutorProfileId),
// same idea as profileMenu.js's top-bar button, but this file doesn't
// depend on that one having run first.

const heroCtaBtn = document.getElementById('heroCtaBtn');
const finalCtaBtn = document.getElementById('finalCtaBtn');
const finalCtaHeadline = document.getElementById('finalCtaHeadline');
const tutorMarqueeTrack = document.getElementById('tutorMarqueeTrack');

// Mirrors marketing_assets_source/cards/{males,females}/*.gif at release
// time - downloaded to ASSETS_DIR/marketing/cards/ at setup, same as the
// avatar/voice/photo assets (see constants.py's ASSETS_DIR) - hardcoded
// rather than listed via an API call, since this is fixed content, not
// user data.
const MALE_TUTORS = ['Brett', 'Dean', 'Ezra', 'Felix', 'Finn', 'Hugo', 'Jasper', 'Kai', 'Leo', 'Max', 'Miles', 'Milo', 'Nico', 'Oscar', 'Theo'];
const FEMALE_TUTORS = ['Ava', 'Chloe', 'Elena', 'Holly', 'Isla', 'Jade', 'Lana', 'Maya', 'Mila', 'Nina', 'Piper', 'Ruby', 'Stella', 'Wren', 'Zoe'];

function setCtaTarget(btn) {
  const hasProfile = !!localStorage.getItem('tutorProfileId');
  btn.textContent = hasProfile ? 'Continue learning' : 'Get started';
  btn.addEventListener('click', () => {
    window.location.href = hasProfile ? '/' : '/get-started';
  });
}
setCtaTarget(heroCtaBtn);
setCtaTarget(finalCtaBtn);
finalCtaHeadline.textContent = localStorage.getItem('tutorProfileId')
  ? 'Pick up right where you left off.'
  : 'Ready when you are.';

// --- Tutor card marquee ---
// Interleaves male/female names (rather than one block of 15 then the
// other) so the visual rhythm reads as one continuous wall of tutors, not
// two halves. The full list is rendered twice back-to-back and the CSS
// animation translates exactly -50% - an infinite loop with no visible
// seam, as long as both halves are pixel-identical, which duplicating
// the same array in code (rather than trying to keep two separate lists
// in sync) guarantees.
function interleave(a, b) {
  const out = [];
  const max = Math.max(a.length, b.length);
  for (let i = 0; i < max; i++) {
    if (a[i]) out.push({ name: a[i], gender: 'males' });
    if (b[i]) out.push({ name: b[i], gender: 'females' });
  }
  return out;
}

function buildTutorCard(tutor) {
  const card = document.createElement('div');
  card.className = 'tutor-card';

  const img = document.createElement('img');
  img.src = `/marketing/cards/${tutor.gender}/${tutor.name}.gif`;
  img.alt = tutor.name;
  img.loading = 'lazy';

  const label = document.createElement('span');
  label.className = 'tutor-card-name';
  label.textContent = tutor.name;

  card.appendChild(img);
  card.appendChild(label);
  return card;
}

function renderTutorMarquee() {
  const tutors = interleave(MALE_TUTORS, FEMALE_TUTORS);
  const fragment = document.createDocumentFragment();
  // Twice, back-to-back - see the comment above.
  [...tutors, ...tutors].forEach((t) => fragment.appendChild(buildTutorCard(t)));
  tutorMarqueeTrack.appendChild(fragment);
}
renderTutorMarquee();
