"""
SQLite-backed conversation memory.

Two different access patterns are in play, so two stores are used rather
than one: `data/profiles.json` keeps the small, frequently-rewritten
metadata (profile identity, mic choice, which conversation is active) - that
part lives in profiles_store.py. This module holds the growing,
append-heavy memory data instead: per-profile conversations, each one's
session-resumption state, its transcript turns, and a rolling summary.
Transcripts are unbounded time-series that would force a full-file rewrite
on every turn if kept in JSON; SQLite turns each turn into a cheap INSERT.

No turns are ever capped or deleted by this module (aside from an explicit
delete_conversation call) - summaries are a bounded *distillation* used to
re-seed fresh sessions, not a replacement for the full history.
"""

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime

from .constants import DATA_DIR

DB_FILE = DATA_DIR / "memory.db"

# How often (in inserted turn rows - two per completed back-and-forth) to
# fold recent turns into the rolling summary. 16 turns × 2 rows = 32.
SUMMARY_FOLD_EVERY_N_TURNS = 32


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Creates tables if they don't already exist, and applies any pending
    ALTER TABLE migrations for columns added since a database was first
    created. Safe to call on every startup - CREATE TABLE IF NOT EXISTS is a
    no-op past the first run, and each migration below no-ops (rather than
    erroring) once its column already exists."""
    conn = _connect()
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
              id TEXT PRIMARY KEY,
              profile_id TEXT NOT NULL,
              name TEXT,
              config_json TEXT NOT NULL,
              resumption_handle TEXT,
              resumption_config_json TEXT,
              created_at TEXT,
              updated_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_conv_profile ON conversations(profile_id);

            CREATE TABLE IF NOT EXISTS turns (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              seq INTEGER NOT NULL,
              role TEXT NOT NULL,
              text TEXT NOT NULL,
              ts INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_turns_conv ON turns(conversation_id, seq);

            CREATE TABLE IF NOT EXISTS summaries (
              conversation_id TEXT PRIMARY KEY,
              summary TEXT NOT NULL,
              based_on_turn INTEGER,
              updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS vocab_mistakes (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              term TEXT NOT NULL,
              note TEXT,
              first_seen_ts INTEGER,
              last_seen_ts INTEGER,
              occurrences INTEGER DEFAULT 1,
              correct_streak INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS lesson_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              ts TEXT,
              summary TEXT
            );
            """
        )
        # ALTER TABLE ADD COLUMN for installs whose vocab_mistakes predates
        # correct_streak - the CREATE TABLE above only takes effect for a
        # brand new table, so existing databases need this run explicitly.
        # SQLite raises OperationalError for a column that already exists;
        # that's the expected/common case on every later startup, so it's
        # swallowed, and anything else is re-raised.
        try:
            conn.execute("ALTER TABLE vocab_mistakes ADD COLUMN correct_streak INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def default_conversation_name(config: dict) -> str:
    target = (config or {}).get("target_language") or "Conversation"
    voice = (config or {}).get("voice_name") or ""
    return f"{target} \u00b7 {voice}" if voice else target


def _row_to_conv(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "profile_id": row["profile_id"],
        "name": row["name"],
        "config": json.loads(row["config_json"]) if row["config_json"] else {},
        "resumption_handle": row["resumption_handle"],
        "resumption_config": json.loads(row["resumption_config_json"]) if row["resumption_config_json"] else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_conversation(profile_id: str, config: dict, name: str | None = None) -> dict:
    cid = str(uuid.uuid4())
    ts = _now_iso()
    name = (name or "").strip() or default_conversation_name(config)
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO conversations "
            "(id, profile_id, name, config_json, resumption_handle, resumption_config_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (cid, profile_id, name, json.dumps(config), None, None, ts, ts),
        )
        conn.commit()
    finally:
        conn.close()
    return get_conversation(cid)


def list_conversations(profile_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM conversations WHERE profile_id=? ORDER BY updated_at DESC",
            (profile_id,),
        ).fetchall()
        return [_row_to_conv(r) for r in rows]
    finally:
        conn.close()


def get_conversation(conversation_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        return _row_to_conv(row) if row else None
    finally:
        conn.close()


def update_conversation(conversation_id: str, name: str | None = None, config: dict | None = None) -> dict | None:
    conn = _connect()
    try:
        current = conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone()
        if not current:
            return None
        new_name = name if name is not None else current["name"]
        new_config_json = json.dumps(config) if config is not None else current["config_json"]
        conn.execute(
            "UPDATE conversations SET name=?, config_json=?, updated_at=? WHERE id=?",
            (new_name, new_config_json, _now_iso(), conversation_id),
        )
        conn.commit()
    finally:
        conn.close()
    return get_conversation(conversation_id)


def delete_conversation(conversation_id: str) -> bool:
    from . import quizzes  # lazy import - quizzes.py imports from this module at load time

    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        conn.execute("DELETE FROM turns WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM summaries WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM vocab_mistakes WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM lesson_log WHERE conversation_id=?", (conversation_id,))
        conn.commit()
        deleted = cur.rowcount > 0
    finally:
        conn.close()
    quizzes.delete_conversation_quizzes(conversation_id)
    return deleted


def set_resumption(conversation_id: str, handle: str | None, config_identity: dict | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE conversations SET resumption_handle=?, resumption_config_json=?, updated_at=? WHERE id=?",
            (
                handle,
                json.dumps(config_identity) if config_identity is not None else None,
                _now_iso(),
                conversation_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def clear_resumption(conversation_id: str) -> None:
    set_resumption(conversation_id, None, None)


def insert_turn(conversation_id: str, role: str, text: str) -> int | None:
    """Inserts one finalized turn row (role is 'user' or 'tutor'). Called once
    per completed turn per speaker (batched), not once per streamed chunk.
    Returns the new seq, or None if there was no text to store."""
    if not text or not text.strip():
        return None
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM turns WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        seq = (row["max_seq"] or 0) + 1
        conn.execute(
            "INSERT INTO turns (conversation_id, seq, role, text, ts) VALUES (?,?,?,?,?)",
            (conversation_id, seq, role, text.strip(), int(time.time())),
        )
        conn.execute(
            "UPDATE conversations SET updated_at=? WHERE id=?",
            (_now_iso(), conversation_id),
        )
        conn.commit()
        return seq
    finally:
        conn.close()


def get_turn_count(conversation_id: str) -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM turns WHERE conversation_id=?",
            (conversation_id,),
        ).fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


def get_turns(conversation_id: str, since_seq: int = 0) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT seq, role, text, ts FROM turns WHERE conversation_id=? AND seq>? ORDER BY seq ASC",
            (conversation_id, since_seq),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_summary(conversation_id: str) -> dict | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM summaries WHERE conversation_id=?", (conversation_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_summary(conversation_id: str, summary_text: str, based_on_turn: int) -> None:
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO summaries (conversation_id, summary, based_on_turn, updated_at)
               VALUES (?,?,?,?)
               ON CONFLICT(conversation_id) DO UPDATE SET
                 summary=excluded.summary,
                 based_on_turn=excluded.based_on_turn,
                 updated_at=excluded.updated_at""",
            (conversation_id, summary_text, based_on_turn, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def upsert_vocab_mistake(conversation_id: str, term: str, note: str | None = None) -> None:
    """Records a vocabulary item or recurring mistake surfaced by a
    summarization pass. Re-seeing the same term (case-insensitive) bumps its
    occurrence count and refreshes its note/last-seen time rather than
    duplicating the row - occurrences is what makes a *recurring* mistake
    visible as recurring."""
    if not term or not term.strip():
        return
    term = term.strip()
    ts = int(time.time())
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM vocab_mistakes WHERE conversation_id=? AND term=? COLLATE NOCASE",
            (conversation_id, term),
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE vocab_mistakes SET note=COALESCE(?, note), last_seen_ts=?, occurrences=occurrences+1 WHERE id=?",
                (note, ts, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO vocab_mistakes (conversation_id, term, note, first_seen_ts, last_seen_ts, occurrences) "
                "VALUES (?,?,?,?,?,1)",
                (conversation_id, term, note, ts, ts),
            )
        conn.commit()
    finally:
        conn.close()


def get_vocab_mistakes(conversation_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT term, note, first_seen_ts, last_seen_ts, occurrences, correct_streak FROM vocab_mistakes "
            "WHERE conversation_id=? ORDER BY last_seen_ts DESC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def record_term_review(conversation_id: str, term: str, correct: bool) -> None:
    """Records the outcome of one quiz answer for a vocabulary term, using
    the same vocab_mistakes table upsert_vocab_mistake writes to, so a
    term's conversation mistakes and quiz mistakes count toward the same
    row rather than two disconnected trackers.

    A wrong answer behaves like upsert_vocab_mistake (bumps occurrences,
    refreshes last_seen_ts) and additionally resets correct_streak to 0.
    A correct answer only has an effect if the term already has a row - a
    term the student has only ever gotten right never needs tracking here
    - and then it just extends correct_streak without touching occurrences.
    """
    if not term or not term.strip():
        return
    term = term.strip()
    ts = int(time.time())
    conn = _connect()
    try:
        existing = conn.execute(
            "SELECT id FROM vocab_mistakes WHERE conversation_id=? AND term=? COLLATE NOCASE",
            (conversation_id, term),
        ).fetchone()
        if correct:
            if existing:
                conn.execute(
                    "UPDATE vocab_mistakes SET correct_streak=correct_streak+1, last_seen_ts=? WHERE id=?",
                    (ts, existing["id"]),
                )
                conn.commit()
            return
        if existing:
            conn.execute(
                "UPDATE vocab_mistakes SET last_seen_ts=?, occurrences=occurrences+1, correct_streak=0 WHERE id=?",
                (ts, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO vocab_mistakes "
                "(conversation_id, term, note, first_seen_ts, last_seen_ts, occurrences, correct_streak) "
                "VALUES (?,?,?,?,?,1,0)",
                (conversation_id, term, "missed in quiz", ts, ts),
            )
        conn.commit()
    finally:
        conn.close()


def get_review_candidates(conversation_id: str, limit: int = 5) -> list[str]:
    """Terms worth quizzing again: missed at least once and not yet answered
    correctly twice in a row since, most-missed and least-recently-seen
    first. Two correct answers in a row for a term retires it from this
    list."""
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT term FROM vocab_mistakes WHERE conversation_id=? AND correct_streak<2 "
            "ORDER BY occurrences DESC, last_seen_ts ASC LIMIT ?",
            (conversation_id, limit),
        ).fetchall()
        return [r["term"] for r in rows]
    finally:
        conn.close()


def append_lesson_log(conversation_id: str, note: str) -> None:
    if not note or not note.strip():
        return
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO lesson_log (conversation_id, ts, summary) VALUES (?,?,?)",
            (conversation_id, _now_iso(), note.strip()),
        )
        conn.commit()
    finally:
        conn.close()


def get_lesson_log(conversation_id: str) -> list[dict]:
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT ts, summary FROM lesson_log WHERE conversation_id=? ORDER BY ts DESC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Profile export / import (backup)
# ---------------------------------------------------------------------------


def export_profile_conversations(profile_id: str) -> list[dict]:
    """Every conversation belonging to this profile, each with its turns,
    summary, vocab_mistakes, lesson_log, and quiz sessions nested under it -
    everything needed to reconstruct them elsewhere via
    import_profile_conversations. Doesn't touch profiles.json or
    voice_enrollment - those are exported separately (see
    profiles_store.py / speech_detection/enrollment.py).

    Row ids that are only ever used for internal ordering/lookup
    (turns.id, quiz_items.id - both AUTOINCREMENT) are deliberately not
    included; conversations.id and quiz_sessions.id (both stable UUIDs)
    are, since import uses them as the actual identity to replace.
    """
    from . import quizzes  # lazy import - quizzes.py imports from this module at load time

    conn = _connect()
    try:
        conv_rows = conn.execute("SELECT * FROM conversations WHERE profile_id=?", (profile_id,)).fetchall()
        conversations = []
        for row in conv_rows:
            conv = _row_to_conv(row)
            cid = conv["id"]
            conv["turns"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT role, text, ts FROM turns WHERE conversation_id=? ORDER BY seq ASC", (cid,)
                ).fetchall()
            ]
            summary_row = conn.execute("SELECT summary, based_on_turn FROM summaries WHERE conversation_id=?", (cid,)).fetchone()
            conv["summary"] = dict(summary_row) if summary_row else None
            conv["vocab_mistakes"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT term, note, first_seen_ts, last_seen_ts, occurrences, correct_streak "
                    "FROM vocab_mistakes WHERE conversation_id=?",
                    (cid,),
                ).fetchall()
            ]
            conv["lesson_log"] = [
                dict(r) for r in conn.execute("SELECT ts, summary FROM lesson_log WHERE conversation_id=?", (cid,)).fetchall()
            ]
            conv["quiz_sessions"] = quizzes.export_conversation_quizzes(cid)
            conversations.append(conv)
        return conversations
    finally:
        conn.close()


def import_profile_conversations(profile_id: str, conversations: list[dict]) -> None:
    """Reverse of export_profile_conversations. Each conversation is fully
    replaced (its existing turns/summary/vocab_mistakes/lesson_log/quiz
    rows deleted first, then the imported set inserted fresh) rather than
    merged, so importing the same backup twice - or importing over a
    conversation that already exists locally under this id - never
    duplicates rows. profile_id comes from the argument, not trusted from
    the payload, so the same export could be imported under a different
    profile id if that's ever needed.

    resumption_handle/resumption_config are intentionally dropped on
    import - a Live API session handle from wherever the backup was made
    is never valid to resume elsewhere.
    """
    from . import quizzes  # lazy import - quizzes.py imports from this module at load time

    conn = _connect()
    try:
        for conv in conversations:
            cid = conv["id"]
            conn.execute("DELETE FROM turns WHERE conversation_id=?", (cid,))
            conn.execute("DELETE FROM summaries WHERE conversation_id=?", (cid,))
            conn.execute("DELETE FROM vocab_mistakes WHERE conversation_id=?", (cid,))
            conn.execute("DELETE FROM lesson_log WHERE conversation_id=?", (cid,))

            conn.execute(
                "INSERT OR REPLACE INTO conversations "
                "(id, profile_id, name, config_json, resumption_handle, resumption_config_json, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (
                    cid,
                    profile_id,
                    conv["name"],
                    json.dumps(conv["config"]),
                    None,
                    None,
                    conv["created_at"],
                    conv["updated_at"],
                ),
            )
            for seq, t in enumerate(conv.get("turns", []), start=1):
                conn.execute(
                    "INSERT INTO turns (conversation_id, seq, role, text, ts) VALUES (?,?,?,?,?)",
                    (cid, seq, t["role"], t["text"], t["ts"]),
                )
            if conv.get("summary"):
                conn.execute(
                    "INSERT INTO summaries (conversation_id, summary, based_on_turn, updated_at) VALUES (?,?,?,?)",
                    (cid, conv["summary"]["summary"], conv["summary"]["based_on_turn"], _now_iso()),
                )
            for v in conv.get("vocab_mistakes", []):
                conn.execute(
                    "INSERT INTO vocab_mistakes "
                    "(conversation_id, term, note, first_seen_ts, last_seen_ts, occurrences, correct_streak) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        cid,
                        v["term"],
                        v["note"],
                        v["first_seen_ts"],
                        v["last_seen_ts"],
                        v["occurrences"],
                        v["correct_streak"],
                    ),
                )
            for lg in conv.get("lesson_log", []):
                conn.execute(
                    "INSERT INTO lesson_log (conversation_id, ts, summary) VALUES (?,?,?)",
                    (cid, lg["ts"], lg["summary"]),
                )
        conn.commit()
    finally:
        conn.close()

    for conv in conversations:
        quizzes.import_conversation_quizzes(conv["id"], conv.get("quiz_sessions", []))
