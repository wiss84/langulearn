"""
SQLite-backed quiz session state.

Shares memory.py's SQLite file (same physical database, a distinct set of
tables) rather than a separate file, but stays a separate module since it's
a distinct concern - structured quiz results, not conversation transcript.

A quiz session is durable from the moment it's created (start_quiz_session),
not just once finished: it's inserted with status='in_progress' holding the
full generated quiz content, and each answer is written as it happens
(record_item_answer) rather than batched at the end. finalize_quiz_session
is the only thing that ever changes its status away from 'in_progress'.
This is what lets get_in_progress_quiz find and resume a quiz that was
never finished, however it was left.
"""

import json
import re
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

from .constants import DATA_DIR
from .memory import record_term_review

DB_FILE = DATA_DIR / "memory.db"


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Creates the quiz tables if they don't already exist. Safe to call on
    every startup."""
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS quiz_sessions (
              id TEXT PRIMARY KEY,
              conversation_id TEXT NOT NULL,
              quiz_type TEXT NOT NULL,
              status TEXT NOT NULL DEFAULT 'in_progress',
              quiz_payload_json TEXT NOT NULL,
              current_index INTEGER NOT NULL DEFAULT 0,
              total_items INTEGER NOT NULL,
              correct_items INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quiz_sessions_conv ON quiz_sessions(conversation_id);

            CREATE TABLE IF NOT EXISTS quiz_items (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              quiz_session_id TEXT NOT NULL,
              item_index INTEGER NOT NULL,
              target_term TEXT NOT NULL,
              prompt_or_text TEXT NOT NULL,
              correct_answer TEXT NOT NULL,
              student_answer TEXT,
              is_correct INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_quiz_items_session ON quiz_items(quiz_session_id);
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def start_quiz_session(conversation_id: str, quiz_type: str, payload: dict) -> str:
    """Creates a new in_progress quiz session with the full generated quiz
    payload (intro message + every item, correct answers included) stored
    verbatim, and returns its id. Meant to be called as soon as a quiz is
    generated, before anything is shown to the student.
    """
    quiz_id = str(uuid.uuid4())
    ts = _now_iso()
    items = payload.get("items") or []
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO quiz_sessions "
            "(id, conversation_id, quiz_type, status, quiz_payload_json, current_index, total_items, correct_items, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (quiz_id, conversation_id, quiz_type, "in_progress", json.dumps(payload), 0, len(items), 0, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return quiz_id


def record_item_answer(
    quiz_id: str,
    item_index: int,
    target_term: str,
    prompt_or_text: str,
    correct_answer: str,
    student_answer: str | None,
    is_correct: bool,
) -> None:
    """Records one answered quiz item and advances the session's progress
    (current_index, correct_items). Meant to be called once per answer, as
    the student answers each slide - not batched at the end - so progress
    made so far is never lost.

    Also feeds record_term_review for the item's target_term, looked up via
    the session's own conversation_id, so a quiz answer's effect on that
    term's spaced-repetition state is recorded immediately rather than only
    once the whole quiz is finished.
    """
    conn = _connect()
    try:
        session_row = conn.execute("SELECT conversation_id FROM quiz_sessions WHERE id=?", (quiz_id,)).fetchone()
        conversation_id = session_row["conversation_id"] if session_row else None
        conn.execute(
            "INSERT INTO quiz_items "
            "(quiz_session_id, item_index, target_term, prompt_or_text, correct_answer, student_answer, is_correct) "
            "VALUES (?,?,?,?,?,?,?)",
            (quiz_id, item_index, target_term, prompt_or_text, correct_answer, student_answer, int(is_correct)),
        )
        conn.execute(
            "UPDATE quiz_sessions SET current_index=?, correct_items=correct_items+?, updated_at=? WHERE id=?",
            (item_index + 1, 1 if is_correct else 0, _now_iso(), quiz_id),
        )
        conn.commit()
    finally:
        conn.close()
    if conversation_id:
        record_term_review(conversation_id, target_term, is_correct)


def finalize_quiz_session(quiz_id: str, status: str = "completed") -> None:
    """Moves a quiz session out of 'in_progress' - 'completed' once the
    student reaches the summary screen, or 'skipped' if they abandon a
    resumed quiz instead. Everything else about the row is left as-is."""
    conn = _connect()
    try:
        conn.execute(
            "UPDATE quiz_sessions SET status=?, updated_at=? WHERE id=?",
            (status, _now_iso(), quiz_id),
        )
        conn.commit()
    finally:
        conn.close()


def _row_to_session(row: sqlite3.Row) -> dict:
    return {
        "quiz_id": row["id"],
        "conversation_id": row["conversation_id"],
        "quiz_type": row["quiz_type"],
        "status": row["status"],
        "payload": json.loads(row["quiz_payload_json"]),
        "current_index": row["current_index"],
        "total_items": row["total_items"],
        "correct_items": row["correct_items"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _get_items(conn: sqlite3.Connection, quiz_session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT item_index, target_term, prompt_or_text, correct_answer, student_answer, is_correct "
        "FROM quiz_items WHERE quiz_session_id=? ORDER BY item_index ASC",
        (quiz_session_id,),
    ).fetchall()
    return [
        {
            "item_index": r["item_index"],
            "target_term": r["target_term"],
            "prompt_or_text": r["prompt_or_text"],
            "correct_answer": r["correct_answer"],
            "student_answer": r["student_answer"],
            "is_correct": bool(r["is_correct"]),
        }
        for r in rows
    ]


def get_in_progress_quiz(conversation_id: str) -> dict | None:
    """Returns the conversation's single in_progress quiz session, with its
    already-recorded answers under "answered_items", or None if there isn't
    one. There should only ever be at most one in_progress session per
    conversation - callers are responsible for checking this before
    starting a new one rather than it being enforced here.

    Excludes quiz_type='review' sessions (Test Yourself, see
    get_reviewable_quiz_items) - this function's only caller is
    live_session.py, for resuming/deduping the LIVE tutor quiz drawer, and
    a review session was never generated by (or meant to be shown inside)
    that flow. Without this exclusion, an abandoned Test Yourself run left
    in_progress could get incorrectly offered to the tutor session as
    something to resume.
    """
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT * FROM quiz_sessions WHERE conversation_id=? AND status='in_progress' AND quiz_type!='review' "
            "ORDER BY created_at DESC LIMIT 1",
            (conversation_id,),
        ).fetchone()
        if not row:
            return None
        session = _row_to_session(row)
        session["answered_items"] = _get_items(conn, session["quiz_id"])
        return session
    finally:
        conn.close()


def get_quiz_sessions(conversation_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM quiz_sessions WHERE conversation_id=? ORDER BY created_at DESC",
            (conversation_id,),
        ).fetchall()
        return [_row_to_session(r) for r in rows]
    finally:
        conn.close()


def get_quiz_items(quiz_session_id: str) -> list[dict]:
    conn = _connect()
    try:
        return _get_items(conn, quiz_session_id)
    finally:
        conn.close()


def compute_quiz_type(items: list[dict]) -> str:
    """Derives the quiz_sessions.quiz_type label (a DB/analytics field
    only - the frontend renders purely from each item's own item_type)
    from what a set of items actually contain. Shared by the tutor's
    start_quiz tool (live_session.py) and the standalone Test Yourself
    review quizzes (get_reviewable_quiz_items below / routes_api.py's
    reviewable-quiz endpoints), so quiz_type stays consistent regardless
    of which caller generated the session.
    """
    item_types = {i.get("item_type") for i in items if isinstance(i, dict)}
    if item_types == {"multiple_choice"}:
        return "multiple_choice"
    if item_types == {"fill_blank_dragdrop"}:
        return "fill_blank_dragdrop"
    if item_types:
        return "mixed"
    return "multiple_choice"  # empty/malformed items - arbitrary fallback, never actually shown


_BLANK_RE = re.compile(r"\{\d+\}")

_REVIEWABLE_RANGE_DELTAS = {
    "today": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
}


def _range_cutoff_iso(range_key: str) -> str | None:
    """None for 'all' (or any unrecognized key - callers validate the
    value themselves, this just degrades to no filtering rather than
    raising). Rolling windows from the current instant, not calendar-day
    boundaries - simpler, and consistent with quiz_sessions.created_at
    being an exact timestamp rather than a date.
    """
    delta = _REVIEWABLE_RANGE_DELTAS.get(range_key)
    if delta is None:
        return None
    return (datetime.now(UTC) - delta).isoformat()


def _is_well_formed_quiz_item(item) -> bool:
    """Mirrors the client-side malformed-item guards in
    quizRenderers.js/quizDragDrop.js, so a broken stored item is dropped
    server-side here rather than reaching the Test Yourself page as a
    blank or broken slide."""
    if not isinstance(item, dict):
        return False
    if not (item.get("question") or "").strip():
        return False
    item_type = item.get("item_type")
    if item_type == "multiple_choice":
        choices = item.get("choices") or []
        idx = item.get("correct_choice_index")
        return len(choices) >= 2 and isinstance(idx, int) and 0 <= idx < len(choices) and bool(choices[idx])
    if item_type == "fill_blank_dragdrop":
        blank_count = len(_BLANK_RE.findall(item.get("text_with_blanks") or ""))
        correct_answers = item.get("correct_answers") or []
        word_bank = item.get("word_bank") or []
        return blank_count > 0 and len(correct_answers) == blank_count and len(word_bank) >= blank_count
    return False


def get_reviewable_quiz_items(conversation_id: str, range_key: str = "all") -> list[dict]:
    """Every quiz item a student has actually answered in this
    conversation, deduplicated by target_term (case-insensitive - same
    key memory.py's vocab_mistakes already uses), for the standalone Test
    Yourself page - not the flattened quiz_items answer log, which lacks
    the choices/word_bank/item_type a slide needs to render again.

    Sessions are read newest-first and only the item_index values that
    genuinely have a quiz_items row (actually shown to and answered by the
    student - an abandoned quiz's unreached items don't count) are pulled
    back out of that session's own payload["items"] for their full
    interactive shape. Reading sessions newest-first and keeping only the
    first occurrence of each term means a term re-quizzed since keeps its
    most recent phrasing/mechanic rather than an arbitrary or oldest one.

    range_key filters which sessions are even considered, by created_at,
    before dedup runs - see _range_cutoff_iso for the accepted values.
    """
    cutoff = _range_cutoff_iso(range_key)
    conn = _connect()
    try:
        query = "SELECT * FROM quiz_sessions WHERE conversation_id=?"
        params: list = [conversation_id]
        if cutoff is not None:
            query += " AND created_at>=?"
            params.append(cutoff)
        query += " ORDER BY created_at DESC"
        session_rows = conn.execute(query, params).fetchall()

        seen_terms: set[str] = set()
        items: list[dict] = []
        for row in session_rows:
            session = _row_to_session(row)
            answered_indices = {
                r["item_index"]
                for r in conn.execute(
                    "SELECT DISTINCT item_index FROM quiz_items WHERE quiz_session_id=?", (session["quiz_id"],)
                ).fetchall()
            }
            payload_items = session["payload"].get("items") or []
            for idx in sorted(answered_indices):
                if idx >= len(payload_items):
                    continue  # answer/payload mismatch - shouldn't happen, skip defensively
                item = payload_items[idx]
                if not _is_well_formed_quiz_item(item):
                    continue
                term_key = (item.get("target_term") or "").strip().casefold()
                if not term_key or term_key in seen_terms:
                    continue
                seen_terms.add(term_key)
                items.append(item)
        return items
    finally:
        conn.close()


def delete_conversation_quizzes(conversation_id: str) -> None:
    """Deletes every quiz session (and its items) belonging to a
    conversation. Called by memory.delete_conversation as part of deleting
    a conversation entirely."""
    conn = _connect()
    try:
        session_ids = [
            r["id"] for r in conn.execute("SELECT id FROM quiz_sessions WHERE conversation_id=?", (conversation_id,)).fetchall()
        ]
        for sid in session_ids:
            conn.execute("DELETE FROM quiz_items WHERE quiz_session_id=?", (sid,))
        conn.execute("DELETE FROM quiz_sessions WHERE conversation_id=?", (conversation_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Profile export / import (backup) - called by memory.py's own
# export_profile_conversations/import_profile_conversations, one
# conversation at a time.
# ---------------------------------------------------------------------------


def export_conversation_quizzes(conversation_id: str) -> list[dict]:
    """Every quiz session for this conversation, each with its items
    nested under it. quiz_items.id (AUTOINCREMENT, internal-only) is left
    out; quiz_sessions.id (a stable UUID) is kept, since import uses it as
    the identity to replace.
    """
    conn = _connect()
    try:
        rows = conn.execute("SELECT * FROM quiz_sessions WHERE conversation_id=?", (conversation_id,)).fetchall()
        sessions = []
        for row in rows:
            session = _row_to_session(row)
            session["items"] = _get_items(conn, session["quiz_id"])
            sessions.append(session)
        return sessions
    finally:
        conn.close()


def import_conversation_quizzes(conversation_id: str, quiz_sessions: list[dict]) -> None:
    """Reverse of export_conversation_quizzes. Every existing quiz session
    (and its items) for this conversation is deleted first, then the
    imported set inserted fresh - a full replace, not a merge, so
    re-importing the same backup never duplicates rows.
    """
    conn = _connect()
    try:
        existing_ids = [
            r["id"] for r in conn.execute("SELECT id FROM quiz_sessions WHERE conversation_id=?", (conversation_id,)).fetchall()
        ]
        for sid in existing_ids:
            conn.execute("DELETE FROM quiz_items WHERE quiz_session_id=?", (sid,))
        conn.execute("DELETE FROM quiz_sessions WHERE conversation_id=?", (conversation_id,))

        for session in quiz_sessions:
            conn.execute(
                "INSERT INTO quiz_sessions "
                "(id, conversation_id, quiz_type, status, quiz_payload_json, current_index, total_items, correct_items, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    session["quiz_id"],
                    conversation_id,
                    session["quiz_type"],
                    session["status"],
                    json.dumps(session["payload"]),
                    session["current_index"],
                    session["total_items"],
                    session["correct_items"],
                    session["created_at"],
                    session["updated_at"],
                ),
            )
            for item in session.get("items", []):
                conn.execute(
                    "INSERT INTO quiz_items "
                    "(quiz_session_id, item_index, target_term, prompt_or_text, correct_answer, student_answer, is_correct) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        session["quiz_id"],
                        item["item_index"],
                        item["target_term"],
                        item["prompt_or_text"],
                        item["correct_answer"],
                        item["student_answer"],
                        int(item["is_correct"]),
                    ),
                )
        conn.commit()
    finally:
        conn.close()
