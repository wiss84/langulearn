// ThirtyTutors - Test Yourself ("quiz mode") page (/quiz-mode). Reached from
// a conversation row's quiz icon on the profile detail modal
// (profileDetail.js) - a deterministic, full-screen replay of every quiz
// item already answered in that conversation, concatenated into one round.
// No tutor/model involvement and no live websocket: this is a standalone
// page, so slide rendering reuses quizRenderers.js/quizDragDrop.js (loaded
// before this file - see quiz_mode.html) but answer submission/session
// tracking is its own light REST-backed flow instead of quizDrawer.js's
// websocket-driven one.
//
// Every run (including Redo) is a genuine new quiz_sessions row
// (quiz_type="review"), created via the reviewable-quiz/start endpoint and
// finalized via .../finish - not a parallel, untracked path. A session
// left mid-quiz when the person navigates away is best-effort finalized
// via sendBeacon on pagehide, so it doesn't linger as in_progress forever;
// live_session.py additionally never resumes/reuses a quiz_type="review"
// session for the live tutor drawer either way, so an unfinalized one here
// can't leak into that flow regardless.

const quizModeLoadingEl = document.getElementById('quizModeLoading');
const quizModeEmptyStateEl = document.getElementById('quizModeEmptyState');
const quizModeEmptyMessageEl = document.getElementById('quizModeEmptyMessage');
const quizModeReadyStateEl = document.getElementById('quizModeReadyState');
const quizModeRangeSelect = document.getElementById('quizModeRangeSelect');
const quizModeCountEl = document.getElementById('quizModeCount');
const quizModeStartBtn = document.getElementById('quizModeStartBtn');

const quizModeIntroEl = document.getElementById('quizModeIntro');
const quizModeSessionEl = document.getElementById('quizModeSession');
const quizModeBackBtn = document.getElementById('quizModeBackBtn');
const quizModeProgressLabelEl = document.getElementById('quizModeProgressLabel');
const quizModeProgressFillEl = document.getElementById('quizModeProgressFill');
const quizModeSlideEl = document.getElementById('quizModeSlide');
const quizModeRedoBtn = document.getElementById('quizModeRedoBtn');
const quizModeNextBtn = document.getElementById('quizModeNextBtn');
const quizModeAnnounceEl = document.getElementById('quizModeAnnounce');
const quizModeIntroCloseBtn = document.getElementById('quizModeIntroCloseBtn');
const quizModeSessionCloseBtn = document.getElementById('quizModeSessionCloseBtn');

const params = new URLSearchParams(window.location.search);
const profileId = params.get('profile_id');
const conversationId = params.get('conversation_id');

let latestFetchedItems = []; // deduped items for the currently-selected range, as returned by the server (unshuffled)
let quiz = null; // null when no session has been started yet this visit - see startQuiz

// Shared by quizRenderers.js/quizDragDrop.js - same role as quizDrawer.js's
// identically-named function, just targeting this page's own live region.
function announceQuizResult(isCorrect) {
  quizModeAnnounceEl.textContent = isCorrect ? 'Correct' : 'Not quite';
}

// Each item self-declares its own item_type (tutor_tools.py's QUIZ_TOOL
// schema) - same fallback as quizDrawer.js's identical helper, for a quiz
// item stored before item_type existed.
function itemQuizType(item) {
  if (item && (item.item_type === 'multiple_choice' || item.item_type === 'fill_blank_dragdrop')) {
    return item.item_type;
  }
  return item && Array.isArray(item.word_bank) && item.word_bank.length ? 'fill_blank_dragdrop' : 'multiple_choice';
}

function shuffleItems(items) {
  const a = items.slice();
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

function isTypingTarget(el) {
  const tag = (el && el.tagName || '').toLowerCase();
  return tag === 'input' || tag === 'textarea' || (el && el.isContentEditable);
}

function navigateAway() {
  // Launched from a conversation row on the profile detail modal - closing
  // returns there (reopened via ?open=, same convention avatarSelect.js's
  // Back button already uses) rather than to the learning page, which
  // isn't where this was reached from.
  window.location.href = `/profiles?open=${encodeURIComponent(profileId)}`;
}

// --- Intro screen ---

async function fetchReviewableItems(range) {
  const res = await fetch(
    `/api/profiles/${profileId}/conversations/${conversationId}/reviewable-quiz?range=${encodeURIComponent(range)}`
  );
  if (!res.ok) throw new Error('Failed to load reviewable quiz items');
  const data = await res.json();
  return data.items || [];
}

function renderIntro(items) {
  latestFetchedItems = items;
  quizModeLoadingEl.style.display = 'none';

  if (items.length === 0) {
    quizModeEmptyStateEl.style.display = '';
    quizModeReadyStateEl.style.display = 'none';
    quizModeEmptyMessageEl.textContent =
      "You haven't completed any quizzes in this conversation yet. Practice with the tutor first, then come back to test yourself.";
    return;
  }

  quizModeEmptyStateEl.style.display = 'none';
  quizModeReadyStateEl.style.display = '';
  quizModeCountEl.textContent = `${items.length} ${items.length === 1 ? 'item' : 'items'} ready to review.`;
}

async function refreshIntro() {
  const requestedRange = quizModeRangeSelect.value;
  quizModeLoadingEl.style.display = '';
  quizModeLoadingEl.textContent = 'Loading...';
  quizModeEmptyStateEl.style.display = 'none';
  quizModeReadyStateEl.style.display = 'none';
  try {
    const items = await fetchReviewableItems(requestedRange);
    // The range select is always interactive (even mid-fetch) so a fast
    // second change here doesn't get clobbered by a slower first response
    // landing after it - only apply a response if it's still what's
    // currently selected.
    if (quizModeRangeSelect.value !== requestedRange) return;
    renderIntro(items);
  } catch (e) {
    if (quizModeRangeSelect.value !== requestedRange) return;
    console.error(e);
    quizModeLoadingEl.style.display = '';
    quizModeLoadingEl.textContent = "Couldn't load your quiz history - check your connection and reload this page.";
  }
}

quizModeRangeSelect.addEventListener('change', refreshIntro);

// --- Session: start/answer/finish REST calls ---

async function startReviewSession(items) {
  const res = await fetch(`/api/profiles/${profileId}/conversations/${conversationId}/reviewable-quiz/start`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ items }),
  });
  if (!res.ok) throw new Error('Failed to start review quiz session');
  const data = await res.json();
  return data.quiz_id;
}

function recordAnswer(itemIndex, item, studentAnswer, isCorrect) {
  const thisItemType = itemQuizType(item);
  const promptOrText = thisItemType === 'multiple_choice' ? (item.question || '') : (item.text_with_blanks || '');
  const correctAnswer =
    thisItemType === 'multiple_choice'
      ? (item.choices || [])[item.correct_choice_index] || ''
      : (item.correct_answers || []).join('|');

  fetch(`/api/profiles/${profileId}/conversations/${conversationId}/reviewable-quiz/${quiz.quizId}/answer`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      item_index: itemIndex,
      target_term: item.target_term,
      prompt_or_text: promptOrText,
      correct_answer: correctAnswer,
      student_answer: studentAnswer,
      is_correct: isCorrect,
    }),
  }).catch((e) => console.error('[quizMode] answer recording failed:', e));
}

async function finalizeSession(quizId, status) {
  await fetch(
    `/api/profiles/${profileId}/conversations/${conversationId}/reviewable-quiz/${quizId}/finish?status=${status}`,
    { method: 'POST' }
  ).catch((e) => console.error('[quizMode] finalize failed:', e));
}

// Best-effort only (pagehide can't reliably await a normal fetch) - covers
// the person navigating away (home link, browser back/close) mid-quiz
// without ever hitting Done or the in-page Close button. See this file's
// header comment for why a lingering in_progress row here is harmless
// either way, this just keeps the data clean.
window.addEventListener('pagehide', () => {
  if (!quiz || !quiz.quizId || quiz.finalized) return;
  const status = quiz.answers.every(Boolean) ? 'completed' : 'skipped';
  navigator.sendBeacon(
    `/api/profiles/${profileId}/conversations/${conversationId}/reviewable-quiz/${quiz.quizId}/finish?status=${status}`,
    new Blob()
  );
});

// --- Slide rendering (mirrors quizDrawer.js's renderCurrentSlide/
// renderSummarySlide, minus the websocket send - see recordAnswer above) ---

function updateProgressUI() {
  const total = quiz.items.length;
  if (quiz.viewingIndex >= total) {
    quizModeProgressLabelEl.textContent = 'Summary';
    quizModeProgressFillEl.style.width = '100%';
  } else {
    quizModeProgressLabelEl.textContent = `${quiz.viewingIndex + 1} of ${total}`;
    quizModeProgressFillEl.style.width = `${(quiz.currentIndex / total) * 100}%`;
  }
  quizModeBackBtn.style.display = quiz.viewingIndex > 0 ? 'inline' : 'none';
}

function renderSummarySlide() {
  const total = quiz.items.length;
  const correct = quiz.answers.filter((a) => a && a.isCorrect).length;
  const missed = quiz.items.filter((_, i) => quiz.answers[i] && !quiz.answers[i].isCorrect);

  quizModeSlideEl.innerHTML = '';
  quizModeSlideEl.className = 'quiz-slide';
  quizModeSlideEl.style.textAlign = 'center';

  const scoreEl = document.createElement('div');
  scoreEl.className = 'quiz-summary-score' + (correct === total ? ' all-correct' : '');
  scoreEl.textContent = `${correct}/${total}`;
  quizModeSlideEl.appendChild(scoreEl);

  const labelEl = document.createElement('p');
  labelEl.className = 'quiz-summary-label';
  labelEl.textContent = missed.length ? 'A couple to review:' : 'Nice work - all correct!';
  quizModeSlideEl.appendChild(labelEl);

  if (missed.length) {
    const listEl = document.createElement('div');
    listEl.className = 'quiz-missed-list';
    missed.forEach((item) => {
      const pill = document.createElement('span');
      pill.className = 'quiz-missed-pill';
      pill.textContent = item.target_term;
      listEl.appendChild(pill);
    });
    quizModeSlideEl.appendChild(listEl);
  }

  quizModeRedoBtn.style.display = '';
  quizModeNextBtn.textContent = 'Done';
  quizModeNextBtn.disabled = false;
}

function renderCurrentSlide() {
  updateProgressUI();
  quizModeRedoBtn.style.display = 'none';

  if (quiz.viewingIndex >= quiz.items.length) {
    renderSummarySlide();
    return;
  }

  const idx = quiz.viewingIndex;
  const item = quiz.items[idx];
  const existingAnswer = quiz.answers[idx];
  const readOnly = !!existingAnswer;

  quizModeSlideEl.innerHTML = '';
  quizModeSlideEl.style.textAlign = '';
  quizModeSlideEl.className = 'quiz-slide';

  function handleAnswer(studentAnswer, isCorrect) {
    if (quiz.answers[idx]) return; // already answered - renderers shouldn't call this twice, but guard anyway
    quiz.answers[idx] = { studentAnswer, isCorrect };
    if (idx >= quiz.currentIndex) quiz.currentIndex = idx + 1;
    recordAnswer(idx, item, studentAnswer, isCorrect);
    quizModeNextBtn.disabled = false;
    updateProgressUI();
  }

  let slideEl;
  if (itemQuizType(item) === 'multiple_choice') {
    slideEl = renderMultipleChoiceSlide(item, existingAnswer, readOnly, handleAnswer);
  } else {
    slideEl = renderFillBlankDragDropSlide(item, existingAnswer, readOnly, handleAnswer);
  }
  if (slideEl) quizModeSlideEl.appendChild(slideEl);

  quizModeNextBtn.textContent = 'Next';
  quizModeNextBtn.disabled = !readOnly;
}

// --- Session lifecycle ---

async function beginRun(items) {
  const quizId = await startReviewSession(items);
  quiz = {
    quizId,
    items,
    currentIndex: 0,
    viewingIndex: 0,
    answers: new Array(items.length).fill(null),
    finalized: false,
  };
  quizModeIntroEl.style.display = 'none';
  quizModeSessionEl.style.display = '';
  renderCurrentSlide();
  requestAnimationFrame(() => {
    const focusable = quizModeSlideEl.querySelector('button, input, [tabindex]');
    if (focusable) focusable.focus();
  });
}

quizModeStartBtn.addEventListener('click', async () => {
  quizModeStartBtn.disabled = true;
  quizModeStartBtn.textContent = 'Starting...';
  try {
    await beginRun(shuffleItems(latestFetchedItems));
  } catch (e) {
    console.error(e);
    quizModeStartBtn.disabled = false;
    quizModeStartBtn.textContent = 'Start Quiz';
    quizModeCountEl.textContent = "Couldn't start the quiz - check your connection and try again.";
  }
});

quizModeNextBtn.addEventListener('click', async () => {
  if (!quiz) return;
  if (quiz.viewingIndex >= quiz.items.length) {
    // Done, from the summary slide - finalize as completed and leave;
    // there's nothing else for this page to show after that.
    quiz.finalized = true;
    await finalizeSession(quiz.quizId, 'completed');
    navigateAway();
    return;
  }
  quiz.viewingIndex += 1;
  renderCurrentSlide();
});

quizModeBackBtn.addEventListener('click', () => {
  if (!quiz || quiz.viewingIndex <= 0) return;
  quiz.viewingIndex -= 1;
  renderCurrentSlide();
});

// Only ever reachable from the summary slide (see renderSummarySlide) -
// finalizes the just-finished run, then immediately starts a fresh one
// over the same fetched item set, re-shuffled. A genuine new run, not a
// resume, per the resolved Redo behavior.
quizModeRedoBtn.addEventListener('click', async () => {
  if (!quiz) return;
  quizModeRedoBtn.disabled = true;
  const finishedQuizId = quiz.quizId;
  quiz.finalized = true;
  try {
    await finalizeSession(finishedQuizId, 'completed');
    await beginRun(shuffleItems(latestFetchedItems));
  } catch (e) {
    console.error(e);
  } finally {
    quizModeRedoBtn.disabled = false;
  }
});

async function closeAndLeave() {
  if (quiz && quiz.quizId && !quiz.finalized) {
    quiz.finalized = true;
    const status = quiz.answers.every(Boolean) ? 'completed' : 'skipped';
    await finalizeSession(quiz.quizId, status);
  }
  navigateAway();
}

quizModeIntroCloseBtn.addEventListener('click', closeAndLeave);
quizModeSessionCloseBtn.addEventListener('click', closeAndLeave);

// Enter advances past an already-answered slide, same two-presses-not-one
// split as quizDrawer.js's identical handler (inputs stopPropagation their
// own Enter for submitting).
document.addEventListener('keydown', (e) => {
  if (!quiz || e.key !== 'Enter' || isTypingTarget(e.target)) return;
  if (!quizModeNextBtn.disabled) {
    e.preventDefault();
    quizModeNextBtn.click();
  }
});

// --- Init ---

async function init() {
  if (!profileId || !conversationId) {
    window.location.href = '/profiles';
    return;
  }
  await refreshIntro();
}
init();
