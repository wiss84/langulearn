// ThirtyTutors - quiz drawer orchestration for the learning page. Owns the
// drawer's open/close state, the current quiz's progress, sending
// quiz_answer/quiz_done/quiz_skip back over the socket, and dispatching to
// quizRenderers.js/quizDragDrop.js for the actual per-type slide content.
//
// Entry point is window.openQuizDrawer(msg, opts), called from
// websocket.js on a quiz_start or quiz_resume message - opts.resumed
// distinguishes the two (a resumed quiz starts at its saved progress with
// earlier slides pre-rendered as answered, rather than at slide 1).

const bodyEl = document.getElementById('body');
const quizDrawerEl = document.getElementById('quizDrawer');
const quizIntroEl = document.getElementById('quizIntro');
const quizSlideEl = document.getElementById('quizSlide');
const quizProgressLabelEl = document.getElementById('quizProgressLabel');
const quizProgressFillEl = document.getElementById('quizProgressFill');
const quizBackBtn = document.getElementById('quizBackBtn');
const quizSkipBtn = document.getElementById('quizSkipBtn');
const quizNextBtn = document.getElementById('quizNextBtn');
const quizAnnounceEl = document.getElementById('quizAnnounce');

// null when the drawer is closed. viewingIndex is which slide is
// currently displayed (0-based; equal to items.length means the summary
// screen); currentIndex is the furthest slide actually reached/answered -
// they diverge when stepping Back to review an earlier, already-answered
// slide. answers[i] is null until slide i is answered, then
// { studentAnswer, isCorrect }.
let quiz = null;

// Shared by quizRenderers.js/quizDragDrop.js - the red/green flash isn't
// the only feedback signal (see the aria-live region in _quiz_drawer.html).
function announceQuizResult(isCorrect) {
  quizAnnounceEl.textContent = isCorrect ? 'Correct' : 'Not quite';
}

// Each item now self-declares its own item_type (tutor_tools.py's
// QUIZ_TOOL schema - required, one of the normalized fields every item
// carries regardless of mechanic) rather than the app having to infer it.
// The old inference (checking for a non-empty word_bank) is kept as a
// fallback only for quizzes generated/stored before item_type existed -
// a mixed quiz (some multiple_choice items, some fill_blank_dragdrop
// items, one start_quiz call) still renders correctly either way, since
// this is checked per item, not per call.
function itemQuizType(item) {
  if (item && (item.item_type === 'multiple_choice' || item.item_type === 'fill_blank_dragdrop')) {
    return item.item_type;
  }
  return item && Array.isArray(item.word_bank) && item.word_bank.length ? 'fill_blank_dragdrop' : 'multiple_choice';
}

function updateProgressUI() {
  const total = quiz.items.length;
  if (quiz.viewingIndex >= total) {
    quizProgressLabelEl.textContent = 'Summary';
    quizProgressFillEl.style.width = '100%';
  } else {
    quizProgressLabelEl.textContent = `${quiz.viewingIndex + 1} of ${total}`;
    quizProgressFillEl.style.width = `${(quiz.currentIndex / total) * 100}%`;
  }
  quizBackBtn.style.display = quiz.viewingIndex > 0 ? 'inline' : 'none';
}

function renderSummarySlide() {
  const total = quiz.items.length;
  const correct = quiz.answers.filter((a) => a && a.isCorrect).length;
  const missed = quiz.items.filter((_, i) => quiz.answers[i] && !quiz.answers[i].isCorrect);

  quizSlideEl.innerHTML = '';
  quizSlideEl.className = 'quiz-slide';
  quizSlideEl.style.textAlign = 'center';

  const scoreEl = document.createElement('div');
  scoreEl.className = 'quiz-summary-score' + (correct === total ? ' all-correct' : '');
  scoreEl.textContent = `${correct}/${total}`;
  quizSlideEl.appendChild(scoreEl);

  const labelEl = document.createElement('p');
  labelEl.className = 'quiz-summary-label';
  labelEl.textContent = missed.length ? 'A couple to review:' : 'Nice work - all correct!';
  quizSlideEl.appendChild(labelEl);

  if (missed.length) {
    const listEl = document.createElement('div');
    listEl.className = 'quiz-missed-list';
    missed.forEach((item) => {
      const pill = document.createElement('span');
      pill.className = 'quiz-missed-pill';
      pill.textContent = item.target_term;
      listEl.appendChild(pill);
    });
    quizSlideEl.appendChild(listEl);
  }

  quizNextBtn.textContent = 'Done';
  quizNextBtn.disabled = false;
}

function renderCurrentSlide() {
  updateProgressUI();

  if (quiz.viewingIndex >= quiz.items.length) {
    renderSummarySlide();
    return;
  }

  const idx = quiz.viewingIndex;
  const item = quiz.items[idx];
  const existingAnswer = quiz.answers[idx];
  const readOnly = !!existingAnswer;

  quizSlideEl.innerHTML = '';
  quizSlideEl.style.textAlign = '';
  quizSlideEl.className = 'quiz-slide';

  function handleAnswer(studentAnswer, isCorrect) {
    if (quiz.answers[idx]) return; // already answered - renderers shouldn't call this twice, but guard anyway
    quiz.answers[idx] = { studentAnswer, isCorrect };
    if (idx >= quiz.currentIndex) quiz.currentIndex = idx + 1;

    const thisItemType = itemQuizType(item);
    // item.prompt is a fallback for quizzes generated before the
    // prompt->question rename (constants.py QUIZ_TOOL) - only ever
    // populated on old, already-stored payloads now.
    const promptOrText = thisItemType === 'multiple_choice' ? (item.question || item.prompt || '') : (item.text_with_blanks || '');
    const correctAnswer =
      thisItemType === 'multiple_choice'
        ? (item.choices || [])[item.correct_choice_index] || ''
        : (item.correct_answers || []).join('|');

    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: 'quiz_answer',
          quiz_id: quiz.quizId,
          item_index: idx,
          target_term: item.target_term,
          prompt_or_text: promptOrText,
          correct_answer: correctAnswer,
          student_answer: studentAnswer,
          is_correct: isCorrect,
        })
      );
    }

    quizNextBtn.disabled = false;
    updateProgressUI();
  }

  let slideEl;
  if (itemQuizType(item) === 'multiple_choice') {
    slideEl = renderMultipleChoiceSlide(item, existingAnswer, readOnly, handleAnswer);
  } else {
    slideEl = renderFillBlankDragDropSlide(item, existingAnswer, readOnly, handleAnswer);
  }
  if (slideEl) quizSlideEl.appendChild(slideEl);

  quizNextBtn.textContent = 'Next';
  quizNextBtn.disabled = !readOnly;
}

function finishQuiz() {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'quiz_done', quiz_id: quiz.quizId }));
  }
  closeQuizDrawer();
}

function closeQuizDrawer() {
  quizActive = false;
  quizDrawerEl.classList.remove('open', 'wide');
  bodyEl.classList.remove('quiz-drawer-open', 'quiz-drawer-wide');
  talkBtn.classList.remove('quiz-disabled');
  handsFreeBtn.classList.remove('quiz-disabled');
  quiz = null;
}

function openQuizDrawer(msg, opts) {
  opts = opts || {};
  const items = msg.items || [];
  const startIndex = opts.resumed ? msg.current_index || 0 : 0;

  quiz = {
    quizId: msg.quiz_id,
    quizType: msg.quiz_type,
    items,
    currentIndex: startIndex,
    viewingIndex: startIndex,
    answers: new Array(items.length).fill(null),
    resumed: !!opts.resumed,
  };
  if (opts.resumed && msg.answered_items) {
    msg.answered_items.forEach((a) => {
      quiz.answers[a.item_index] = { studentAnswer: a.student_answer, isCorrect: a.is_correct };
    });
  }

  quizActive = true;
  talkBtn.classList.add('quiz-disabled');
  handsFreeBtn.classList.add('quiz-disabled');

  bodyEl.classList.add('quiz-drawer-open');
  // Widened if ANY slide needs the drag tray, not just when every slide
  // does - a mixed quiz with even one dragdrop item still needs the room
  // (see itemQuizType above for why per-item detection replaced the old
  // single quiz.quizType check here).
  const hasDragDropItem = items.some((it) => itemQuizType(it) === 'fill_blank_dragdrop');
  quizDrawerEl.classList.toggle('wide', hasDragDropItem);
  bodyEl.classList.toggle('quiz-drawer-wide', hasDragDropItem);
  quizDrawerEl.classList.add('open');

  // Deliberately NOT sourced from the tutor (see QUIZ_TOOL/QUIZ_INSTRUCTION
  // in constants.py, which no longer even declare an intro_message
  // parameter) - ThirtyTutors only has two quiz mechanics, so this header is
  // fully deterministic from what's actually on the slides, rather than
  // trusted to the model's own wording (which used to leak quiz content,
  // e.g. naming the very word being tested).
  quizIntroEl.textContent = hasDragDropItem
    ? 'Drag the correct words into place, or tap a word then tap a blank.'
    : 'Choose the correct answer.';
  // Always visible now, not just for resumed quizzes - a fresh quiz can
  // hit a malformed slide (see the broken-item fallback in
  // quizRenderers.js/quizDragDrop.js) with no other way out, since Next
  // stays disabled until something valid is answered.
  quizSkipBtn.style.display = 'inline';

  renderCurrentSlide();
  requestAnimationFrame(() => {
    const focusable = quizSlideEl.querySelector('button, input, [tabindex]');
    if (focusable) focusable.focus();
  });
}
window.openQuizDrawer = openQuizDrawer;

quizNextBtn.addEventListener('click', () => {
  if (!quiz) return;
  if (quiz.viewingIndex >= quiz.items.length) {
    finishQuiz();
    return;
  }
  quiz.viewingIndex += 1;
  renderCurrentSlide();
});

quizBackBtn.addEventListener('click', () => {
  if (!quiz || quiz.viewingIndex <= 0) return;
  quiz.viewingIndex -= 1;
  renderCurrentSlide();
});

quizSkipBtn.addEventListener('click', () => {
  if (!quiz) return;
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: 'quiz_skip', quiz_id: quiz.quizId }));
  }
  closeQuizDrawer();
});

// Enter advances past an already-answered slide. Inputs handle their own
// Enter to submit (quizRenderers.js/quizDragDrop.js call stopPropagation
// so that keystroke doesn't also reach here) - a second Enter is what
// advances, so submitting and advancing are deliberately two separate
// presses rather than one doing both.
document.addEventListener('keydown', (e) => {
  if (!quiz || e.key !== 'Enter' || isTypingTarget(e.target)) return;
  if (!quizNextBtn.disabled) {
    e.preventDefault();
    quizNextBtn.click();
  }
});
