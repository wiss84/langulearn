// ThirtyTutors - Settings modal's Stats tab (see settings.js's
// openSettingsModal, which calls populateStatsPane below whenever the
// modal opens - same "populate every pane up front" pattern every other
// tab already uses, not a lazy per-tab-click fetch). Reads currentProfile
// straight from state.js's global, like every other file in this pair of
// <script src> tags on the learning page - see settings.js's own header
// comment for why that's fine here.

const statsStreakValue = document.getElementById('statsStreakValue');
const statsHoursValue = document.getElementById('statsHoursValue');
const statsLanguagesValue = document.getElementById('statsLanguagesValue');
const statsReviewReminder = document.getElementById('statsReviewReminder');
const statsLanguageList = document.getElementById('statsLanguageList');

function formatStudyHours(totalSeconds) {
  const hours = totalSeconds / 3600;
  if (hours < 0.05) return '0';
  return hours < 10 ? hours.toFixed(1) : String(Math.round(hours));
}

// A single labeled horizontal progress bar - shared shape for both the
// vocab-tier bar and the quiz-accuracy bar below, since both are just
// "a fraction, with a caption" - only the numbers/caption text differ.
function buildStatBar(caption, fraction) {
  const wrap = document.createElement('div');
  wrap.className = 'stats-bar-wrap';

  const captionEl = document.createElement('p');
  captionEl.className = 'stats-bar-caption';
  captionEl.textContent = caption;
  wrap.appendChild(captionEl);

  const track = document.createElement('div');
  track.className = 'stats-bar-track';
  const fill = document.createElement('div');
  fill.className = 'stats-bar-fill';
  fill.style.width = `${Math.max(0, Math.min(100, fraction * 100))}%`;
  track.appendChild(fill);
  wrap.appendChild(track);

  return wrap;
}

function buildLanguageCard(bucket) {
  const card = document.createElement('div');
  card.className = 'stats-language-card';

  const header = document.createElement('div');
  header.className = 'stats-language-header';
  const title = document.createElement('span');
  title.className = 'stats-language-title';
  title.textContent = bucket.target_language;
  const meta = document.createElement('span');
  meta.className = 'stats-language-meta';
  meta.textContent = `${bucket.user_turns} exchanges`;
  header.appendChild(title);
  header.appendChild(meta);
  card.appendChild(header);

  card.appendChild(
    buildStatBar(
      `${bucket.vocab_mastered} words mastered (next milestone at ${bucket.vocab_next_tier})`,
      bucket.vocab_mastered / bucket.vocab_next_tier
    )
  );

  if (bucket.quiz_sessions_completed > 0) {
    card.appendChild(
      buildStatBar(
        `${bucket.quiz_accuracy_pct}% quiz accuracy (${bucket.quiz_sessions_completed} quiz${bucket.quiz_sessions_completed === 1 ? '' : 'zes'})`,
        bucket.quiz_accuracy_pct / 100
      )
    );
  } else {
    const noQuiz = document.createElement('p');
    noQuiz.className = 'field-hint';
    noQuiz.textContent = 'No quizzes completed yet.';
    card.appendChild(noQuiz);
  }

  const badges = document.createElement('div');
  badges.className = 'stats-language-badges';
  if (bucket.perfect_quizzes > 0) {
    const badge = document.createElement('span');
    badge.className = 'stats-badge';
    badge.textContent = `\ud83c\udfc6 ${bucket.perfect_quizzes} perfect quiz${bucket.perfect_quizzes === 1 ? '' : 'zes'}`;
    badges.appendChild(badge);
  }
  if (bucket.review_sessions_completed > 0) {
    const badge = document.createElement('span');
    badge.className = 'stats-badge';
    badge.textContent = `\ud83e\udde9 ${bucket.review_sessions_completed} Test Yourself round${bucket.review_sessions_completed === 1 ? '' : 's'}`;
    badges.appendChild(badge);
  }
  if (badges.children.length) card.appendChild(badges);

  return card;
}

function renderStatsPane(data) {
  statsStreakValue.textContent = String(data.current_streak || 0);
  statsHoursValue.textContent = formatStudyHours(data.total_seconds_studied || 0);
  statsLanguagesValue.textContent = String(data.languages_practiced || 0);

  if (data.review_candidates_total > 0) {
    statsReviewReminder.hidden = false;
    statsReviewReminder.textContent =
      `${data.review_candidates_total} word${data.review_candidates_total === 1 ? '' : 's'} ready for review - the tutor will work them back in, or try Test Yourself on that language's conversation.`;
  } else {
    statsReviewReminder.hidden = true;
  }

  statsLanguageList.innerHTML = '';
  if (!data.languages || data.languages.length === 0) {
    const empty = document.createElement('p');
    empty.className = 'field-hint';
    empty.textContent = 'Start a conversation to see your stats here.';
    statsLanguageList.appendChild(empty);
    return;
  }
  data.languages.forEach((bucket) => statsLanguageList.appendChild(buildLanguageCard(bucket)));
}

async function populateStatsPane() {
  if (!currentProfile) return;
  statsLanguageList.innerHTML = '<p class="field-hint">Loading...</p>';
  try {
    const res = await fetch(`/api/profiles/${currentProfile.id}/stats`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderStatsPane(data);
  } catch (e) {
    statsLanguageList.innerHTML = '<p class="field-hint">Could not load stats - check your connection.</p>';
  }
}
