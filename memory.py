"""
SQLite-backed conversation memory.

Two different access patterns are in play, so two stores are used rather
than one: `data/profiles.json` keeps the small, frequently-rewritten
metadata (profile identity, mic choice, which conversation is active) - that
part is unchanged and lives in main.py. This module holds the growing,
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
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
DB_FILE = DATA_DIR / "memory.db"

# How often (in inserted turn rows - two per completed back-and-forth) to
# fold recent turns into the rolling summary. Chosen as a reasonable
# starting point; not load-bearing enough to need to be configurable yet.
SUMMARY_FOLD_EVERY_N_TURNS = 15


def _connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Creates tables if they don't already exist. Safe to call on every
    startup - no migration tooling needed beyond CREATE TABLE IF NOT EXISTS
    for a single-user local app."""
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
              occurrences INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS lesson_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              conversation_id TEXT NOT NULL,
              ts TEXT,
              summary TEXT
            );
            """
        )
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            "SELECT * FROM conversations WHERE profile_id=? ORDER BY updated_at DESC", (profile_id,)
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
    conn = _connect()
    try:
        cur = conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
        conn.execute("DELETE FROM turns WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM summaries WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM vocab_mistakes WHERE conversation_id=?", (conversation_id,))
        conn.execute("DELETE FROM lesson_log WHERE conversation_id=?", (conversation_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def set_resumption(conversation_id: str, handle: str | None, config_identity: dict | None) -> None:
    conn = _connect()
    try:
        conn.execute(
            "UPDATE conversations SET resumption_handle=?, resumption_config_json=?, updated_at=? WHERE id=?",
            (handle, json.dumps(config_identity) if config_identity is not None else None, _now_iso(), conversation_id),
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
            "SELECT COALESCE(MAX(seq), 0) AS max_seq FROM turns WHERE conversation_id=?", (conversation_id,)
        ).fetchone()
        seq = (row["max_seq"] or 0) + 1
        conn.execute(
            "INSERT INTO turns (conversation_id, seq, role, text, ts) VALUES (?,?,?,?,?)",
            (conversation_id, seq, role, text.strip(), int(time.time())),
        )
        conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now_iso(), conversation_id))
        conn.commit()
        return seq
    finally:
        conn.close()


def get_turn_count(conversation_id: str) -> int:
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM turns WHERE conversation_id=?", (conversation_id,)
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
            "SELECT term, note, first_seen_ts, last_seen_ts, occurrences FROM vocab_mistakes "
            "WHERE conversation_id=? ORDER BY last_seen_ts DESC",
            (conversation_id,),
        ).fetchall()
        return [dict(r) for r in rows]
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
