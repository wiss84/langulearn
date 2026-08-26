// ThirtyTutors - conversation notes modal (vocab/mistakes + lesson log).
// Lives on the /profiles page now - each language row in the profile
// detail modal (see profileDetail.js) has its own notes button, calling
// openNotesModal(profileId, conversationId, label) for that conversation
// specifically. No dependency on any "current" profile/conversation
// globals, since /profiles doesn't track an active session the way the
// learning page does.

const notesModalOverlay = document.getElementById('notesModalOverlay');
const notesModalSubtitle = document.getElementById('notesModalSubtitle');
const notesVocabList = document.getElementById('notesVocabList');
const notesLessonList = document.getElementById('notesLessonList');
const closeNotesBtn = document.getElementById('closeNotesBtn');

function formatTimestamp(value) {
  if (!value) return '';
  // ts on vocab_mistakes rows is unix seconds; ts on lesson_log rows is an
  // ISO string - Date() handles both, but only multiply the numeric case.
  const date = typeof value === 'number' ? new Date(value * 1000) : new Date(value);
  if (isNaN(date.getTime())) return '';
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function renderNotesList(container, items, emptyText, renderItem) {
  container.innerHTML = '';
  if (!items || items.length === 0) {
    const empty = document.createElement('div');
    empty.className = 'notes-empty';
    empty.textContent = emptyText;
    container.appendChild(empty);
    return;
  }
  items.forEach((item) => container.appendChild(renderItem(item)));
}

async function openNotesModal(profileId, conversationId, label) {
  notesModalSubtitle.textContent = label || '';
  notesVocabList.innerHTML = '';
  notesLessonList.innerHTML = '';
  notesModalOverlay.classList.add('visible');
  // Forces an immediate repaint - see profileDetail.js's openProfileDetail
  // for the full explanation (a display:none -> flex toggle on a
  // position:fixed overlay wasn't reliably painting until something else
  // forced a recomposite).
  void notesModalOverlay.offsetHeight;

  const res = await fetch(`/api/profiles/${profileId}/conversations/${conversationId}/notes`);
  if (!res.ok) return;
  const data = await res.json();

  renderNotesList(notesVocabList, data.vocab_mistakes, 'Nothing tracked yet - this fills in as the rolling summary runs.', (v) => {
    const el = document.createElement('div');
    el.className = 'notes-item';
    const title = document.createElement('div');
    title.className = 'notes-item-title';
    title.textContent = v.term;
    el.appendChild(title);
    if (v.note) {
      const note = document.createElement('div');
      note.textContent = v.note;
      el.appendChild(note);
    }
    const meta = document.createElement('div');
    meta.className = 'notes-item-meta';
    meta.textContent = `seen ${v.occurrences}x - last ${formatTimestamp(v.last_seen_ts)}`;
    el.appendChild(meta);
    return el;
  });

  renderNotesList(notesLessonList, data.lesson_log, 'No lesson log entries yet.', (l) => {
    const el = document.createElement('div');
    el.className = 'notes-item';
    const meta = document.createElement('div');
    meta.className = 'notes-item-meta';
    meta.textContent = formatTimestamp(l.ts);
    el.appendChild(meta);
    const body = document.createElement('div');
    body.textContent = l.summary;
    el.appendChild(body);
    return el;
  });
}

function closeNotesModal() {
  notesModalOverlay.classList.remove('visible');
}

closeNotesBtn.addEventListener('click', closeNotesModal);
notesModalOverlay.addEventListener('click', (e) => {
  if (e.target === notesModalOverlay) closeNotesModal();
});
