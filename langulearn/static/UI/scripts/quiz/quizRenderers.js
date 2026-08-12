// LanguLearn - quiz drawer slide rendering for multiple_choice (see
// quizDragDrop.js for fill_blank_dragdrop, and quizDrawer.js for the
// drawer chrome/orchestration that calls this).
//
// Builds and returns a DOM element for one slide. If existingAnswer is
// given the slide renders read-only in its already-answered state (used
// both for stepping back to review a past slide and for resuming a quiz
// from an earlier app session). onAnswer(studentAnswer, isCorrect) is
// called exactly once, the first time the student answers.
// announceQuizResult (quizDrawer.js) drives the aria-live feedback shared
// by every quiz type.

function renderMultipleChoiceSlide(item, existingAnswer, readOnly, onAnswer) {
  const wrap = document.createElement('div');

  const choices = item.choices || [];
  const correctIndex = item.correct_choice_index;
  // item.prompt is a fallback for quizzes generated before the
  // prompt->question rename (tutor_tools.py's QUIZ_TOOL) - only ever
  // populated on old, already-stored payloads now.
  const questionText = item.question || item.prompt || '';

  // Same defense-in-depth as quizDragDrop.js's malformed-item check - a
  // blank slide is never acceptable regardless of cause.
  if (!questionText.trim() || choices.length < 2 || correctIndex == null || !choices[correctIndex]) {
    const broken = document.createElement('div');
    broken.className = 'quiz-slide-broken';
    const msg = document.createElement('p');
    msg.className = 'quiz-prompt';
    msg.textContent = "This slide couldn't load properly - use Skip or Back to continue.";
    broken.appendChild(msg);
    console.error('[quizRenderers] malformed item, skipping render:', item);
    return broken;
  }

  const prompt = document.createElement('p');
  prompt.className = 'quiz-prompt';
  prompt.textContent = questionText;
  wrap.appendChild(prompt);

  const list = document.createElement('div');
  list.className = 'quiz-choice-list';

  choices.forEach((choiceText, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'quiz-choice';
    btn.textContent = choiceText;

    if (readOnly) {
      btn.disabled = true;
      if (i === correctIndex) btn.classList.add('correct');
      if (existingAnswer && existingAnswer.studentAnswer === choiceText && !existingAnswer.isCorrect) {
        btn.classList.add('incorrect');
      }
    } else {
      btn.addEventListener('click', () => {
        const isCorrect = i === correctIndex;
        // Every choice locks immediately, and the correct one is
        // highlighted either way - so a wrong pick always reveals the
        // right answer, not just a red flash with no resolution.
        Array.from(list.children).forEach((el, j) => {
          el.disabled = true;
          if (j === correctIndex) el.classList.add('correct');
        });
        if (!isCorrect) btn.classList.add('incorrect');
        btn.classList.add(isCorrect ? 'quiz-flash-correct' : 'quiz-flash-incorrect');
        announceQuizResult(isCorrect);
        onAnswer(choiceText, isCorrect);
      });
    }
    list.appendChild(btn);
  });

  wrap.appendChild(list);
  return wrap;
}
