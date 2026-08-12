// LanguLearn - fill_blank_dragdrop slide rendering (quizDrawer.js calls
// this for that quiz_type; see quizRenderers.js for the other two types).
//
// Two input methods are both always available - neither is a hidden
// fallback for the other:
//   1. Pointer-based drag (pointerdown/pointermove/pointerup with
//      bounding-box hit-testing against the drop slots) - deliberately
//      NOT native HTML5 drag-and-drop, which is known to be flaky inside
//      embedded webviews like pywebview's.
//   2. Click a chip to select it, then click a slot to place it - this
//      doubles as the keyboard/accessibility path (Enter/Space activate
//      a focused chip or slot the same as a click), and is also just a
//      more reliable fallback if the pointer-drag interaction turns out
//      to have rough edges on a given platform.

function renderFillBlankDragDropSlide(item, existingAnswer, readOnly, onAnswer) {
  const wrap = document.createElement('div');

  const template = item.text_with_blanks || '';
  const correctAnswers = item.correct_answers || [];
  const wordBank = item.word_bank || [];
  const blankCount = (template.match(/\{\d+\}/g) || []).length;

  // Defense-in-depth against a malformed item from the model (missing
  // text_with_blanks, or a word_bank too short to cover every blank) -
  // tutor_tools.py's QUIZ_TOOL schema makes every field required so this
  // should be rare, but a blank, silently-broken slide is never an
  // acceptable failure mode regardless of cause, so this is checked
  // defensively rather than trusted.
  if (blankCount === 0 || wordBank.length < blankCount) {
    const broken = document.createElement('div');
    broken.className = 'quiz-slide-broken';
    const msg = document.createElement('p');
    msg.className = 'quiz-prompt';
    msg.textContent = "This slide couldn't load properly - use Skip or Back to continue.";
    broken.appendChild(msg);
    console.error('[quizDragDrop] malformed item, skipping render:', item);
    return broken;
  }

  // item.question is the native-language context for this specific item
  // (tutor_tools.py's QUIZ_TOOL schema) - e.g. "How do you say your name
  // in Polish?" above the blanks/word bank. Falls back to a generic
  // instruction only for quizzes stored before this field existed.
  const prompt = document.createElement('p');
  prompt.className = 'quiz-prompt';
  prompt.textContent = item.question || 'Drag the missing words into place:';
  wrap.appendChild(prompt);

  const textEl = document.createElement('p');
  textEl.className = 'quiz-blank-text';

  const parts = template.split(/(\{\d+\})/g);
  const slots = []; // { el, correctText, filledWith }

  parts.forEach((part) => {
    const match = part.match(/^\{(\d+)\}$/);
    if (!match) {
      textEl.appendChild(document.createTextNode(part));
      return;
    }
    const blankIndex = parseInt(match[1], 10);
    const slotEl = document.createElement('span');
    slotEl.className = 'quiz-drop-slot';
    textEl.appendChild(slotEl);
    slots.push({ el: slotEl, correctText: correctAnswers[blankIndex] || '', filledWith: null });
  });

  wrap.appendChild(textEl);

  if (readOnly) {
    const given = existingAnswer ? (existingAnswer.studentAnswer || '').split('|') : [];
    slots.forEach((slot, i) => {
      slot.el.textContent = given[i] || '';
      slot.el.classList.add('filled', existingAnswer && existingAnswer.isCorrect ? 'correct' : 'incorrect');
    });
    return wrap;
  }

  const tray = document.createElement('div');
  tray.className = 'quiz-dragdrop-tray';
  let selectedChip = null; // click-to-place state

  // NFC-normalizes before lowercasing/trimming so a word_bank/
  // correct_answers entry encoded as combining-character NFD (common with
  // accented languages like Spanish/French) still matches a visually
  // identical NFC-encoded placement - see design_plans/issues_fix.md.
  // Deliberately does NOT strip accents: "cafe" should still not match
  // "café", only the encoding is normalized, not the spelling.
  function normalizeForComparison(str) {
    return (str || '').normalize('NFC').trim().toLowerCase();
  }

  function selectChip(chip) {
    if (selectedChip) selectedChip.classList.remove('selected');
    if (selectedChip === chip) { selectedChip = null; return; }
    selectedChip = chip;
    chip.classList.add('selected');
  }

  function trySubmit() {
    if (slots.some((s) => s.filledWith === null)) return; // wait until every blank is filled
    const studentAnswer = slots.map((s) => s.filledWith).join('|');
    const isCorrect = slots.every((s) => normalizeForComparison(s.filledWith) === normalizeForComparison(s.correctText));
    const chips = Array.from(tray.children);
    slots.forEach((s) => {
      const thisOneCorrect = normalizeForComparison(s.filledWith) === normalizeForComparison(s.correctText);
      s.el.classList.remove('drag-over');
      s.el.classList.add(thisOneCorrect ? 'correct' : 'incorrect');
      s.el.classList.add(thisOneCorrect ? 'quiz-flash-correct' : 'quiz-flash-incorrect');
      // Deliberately never overwrites s.el.textContent here - the word the
      // student actually placed stays visible (right or wrong), matching
      // every other quiz type's read-only rendering. A miss instead
      // highlights the correct word still sitting unplaced in the tray, if
      // there is one, so the answer is shown without erasing the attempt.
      if (!thisOneCorrect) {
        const correctChip = chips.find(
          (c) => !c.classList.contains('placed') && normalizeForComparison(c.textContent) === normalizeForComparison(s.correctText)
        );
        if (correctChip) correctChip.classList.add('correct-reveal');
      }
    });
    announceQuizResult(isCorrect);
    onAnswer(studentAnswer, isCorrect);
  }

  function placeChip(chip, slot) {
    if (slot.filledWith !== null || chip.classList.contains('placed')) return;
    slot.filledWith = chip.textContent;
    slot.el.textContent = chip.textContent;
    slot.el.classList.add('filled');
    chip.classList.add('placed');
    chip.tabIndex = -1;
    if (selectedChip === chip) { chip.classList.remove('selected'); selectedChip = null; }
    trySubmit();
  }

  wordBank.forEach((word) => {
    const chip = document.createElement('span');
    chip.className = 'quiz-drag-chip';
    chip.textContent = word;
    chip.tabIndex = 0;

    chip.addEventListener('click', () => {
      if (!chip.classList.contains('placed')) selectChip(chip);
    });
    chip.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); chip.click(); }
    });

    // Pointer-based drag - a plain click (no meaningful movement) falls
    // through to the click listener above instead, so both paths coexist
    // on the same element without conflicting.
    chip.addEventListener('pointerdown', (e) => {
      if (chip.classList.contains('placed')) return;
      e.preventDefault();
      const startX = e.clientX;
      const startY = e.clientY;
      const rect = chip.getBoundingClientRect();
      let dragging = false;

      function onMove(ev) {
        if (!dragging && Math.hypot(ev.clientX - startX, ev.clientY - startY) > 4) {
          dragging = true;
          chip.classList.add('dragging');
          chip.style.position = 'fixed';
          chip.style.zIndex = '1000';
          chip.style.width = rect.width + 'px';
        }
        if (!dragging) return;
        chip.style.left = ev.clientX - rect.width / 2 + 'px';
        chip.style.top = ev.clientY - rect.height / 2 + 'px';
        slots.forEach((s) => {
          const sr = s.el.getBoundingClientRect();
          const over = ev.clientX >= sr.left && ev.clientX <= sr.right && ev.clientY >= sr.top && ev.clientY <= sr.bottom;
          s.el.classList.toggle('drag-over', over && s.filledWith === null);
        });
      }

      function onUp(ev) {
        document.removeEventListener('pointermove', onMove);
        document.removeEventListener('pointerup', onUp);
        if (!dragging) return; // no real movement - let the click listener above handle it
        chip.classList.remove('dragging');
        chip.style.position = '';
        chip.style.zIndex = '';
        chip.style.left = '';
        chip.style.top = '';
        chip.style.width = '';
        slots.forEach((s) => s.el.classList.remove('drag-over'));
        const target = slots.find((s) => {
          const sr = s.el.getBoundingClientRect();
          return (
            s.filledWith === null &&
            ev.clientX >= sr.left && ev.clientX <= sr.right &&
            ev.clientY >= sr.top && ev.clientY <= sr.bottom
          );
        });
        if (target) placeChip(chip, target);
      }

      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });

    tray.appendChild(chip);
  });

  slots.forEach((slot) => {
    slot.el.tabIndex = 0;
    slot.el.addEventListener('click', () => {
      if (selectedChip && slot.filledWith === null) placeChip(selectedChip, slot);
    });
    slot.el.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); slot.el.click(); }
    });
  });

  wrap.appendChild(tray);
  return wrap;
}
