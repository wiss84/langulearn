"""Integration tests for memory.py's SQLite layer - conversation CRUD,
turns, rolling summaries, vocab/mistakes, and lesson log. Uses the real
SQLite file (isolated per test via conftest.py's isolated_data_dir), not a
mock - this module's whole job IS the SQL, so exercising it for real is the
point.
"""

import pytest

from thirtytutors import memory

pytestmark = pytest.mark.integration


# --- default_conversation_name ---


def test_default_conversation_name_with_voice():
    name = memory.default_conversation_name({"target_language": "Spanish", "voice_name": "Kore"})
    assert name == "Spanish \u00b7 Kore"


def test_default_conversation_name_without_voice():
    assert memory.default_conversation_name({"target_language": "Spanish"}) == "Spanish"


def test_default_conversation_name_falls_back_to_conversation():
    assert memory.default_conversation_name({}) == "Conversation"


# --- Conversation CRUD ---


def test_create_and_get_conversation():
    config = {
        "target_language": "Spanish",
        "voice_name": "Kore",
        "difficulty": "beginner",
    }
    conv = memory.create_conversation("profile-1", config)

    assert conv["profile_id"] == "profile-1"
    assert conv["name"] == "Spanish \u00b7 Kore"
    assert conv["config"] == config
    assert conv["resumption_handle"] is None
    assert conv["resumption_config"] is None

    fetched = memory.get_conversation(conv["id"])
    assert fetched == conv


def test_create_conversation_with_explicit_name():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"}, name="  My custom name  ")
    assert conv["name"] == "My custom name"


def test_get_conversation_returns_none_for_unknown_id():
    assert memory.get_conversation("does-not-exist") is None


def test_list_conversations_scoped_to_profile_ordered_by_updated():
    c1 = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.create_conversation("profile-2", {"target_language": "French"})  # different profile
    c3 = memory.create_conversation("profile-1", {"target_language": "German"})

    results = memory.list_conversations("profile-1")
    ids = [c["id"] for c in results]
    assert set(ids) == {c1["id"], c3["id"]}
    # Most-recently-updated first - c3 was created (and therefore updated) last.
    assert ids[0] == c3["id"]


def test_update_conversation_name_only():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    updated = memory.update_conversation(conv["id"], name="Renamed")
    assert updated["name"] == "Renamed"
    assert updated["config"] == conv["config"]  # untouched


def test_update_conversation_config_only():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish", "difficulty": "beginner"})
    new_config = {**conv["config"], "difficulty": "advanced"}
    updated = memory.update_conversation(conv["id"], config=new_config)
    assert updated["config"]["difficulty"] == "advanced"
    assert updated["name"] == conv["name"]  # untouched


def test_update_conversation_returns_none_for_unknown_id():
    assert memory.update_conversation("nope", name="x") is None


def test_delete_conversation_removes_everything():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.insert_turn(conv["id"], "user", "hola")
    memory.upsert_summary(conv["id"], "summary text", based_on_turn=1)
    memory.upsert_vocab_mistake(conv["id"], "hola")
    memory.append_lesson_log(conv["id"], "covered greetings")

    assert memory.delete_conversation(conv["id"]) is True
    assert memory.get_conversation(conv["id"]) is None
    assert memory.get_turns(conv["id"]) == []
    assert memory.get_summary(conv["id"]) is None
    assert memory.get_vocab_mistakes(conv["id"]) == []
    assert memory.get_lesson_log(conv["id"]) == []


def test_delete_conversation_returns_false_for_unknown_id():
    assert memory.delete_conversation("nope") is False


# --- Turns ---


def test_insert_turn_increments_seq_and_updates_conversation_timestamp():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    seq1 = memory.insert_turn(conv["id"], "user", "hola")
    seq2 = memory.insert_turn(conv["id"], "tutor", "\u00a1hola! \u00bfc\u00f3mo est\u00e1s?")
    assert seq1 == 1
    assert seq2 == 2

    turns = memory.get_turns(conv["id"])
    assert [t["role"] for t in turns] == ["user", "tutor"]
    assert memory.get_turn_count(conv["id"]) == 2


def test_insert_turn_skips_blank_text():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    assert memory.insert_turn(conv["id"], "user", "   ") is None
    assert memory.insert_turn(conv["id"], "user", "") is None
    assert memory.get_turn_count(conv["id"]) == 0


def test_get_turns_since_seq_only_returns_newer_rows():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.insert_turn(conv["id"], "user", "one")
    memory.insert_turn(conv["id"], "user", "two")
    memory.insert_turn(conv["id"], "user", "three")

    newer = memory.get_turns(conv["id"], since_seq=1)
    assert [t["text"] for t in newer] == ["two", "three"]


# --- Summaries ---


def test_summary_upsert_and_get():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    assert memory.get_summary(conv["id"]) is None

    memory.upsert_summary(conv["id"], "first summary", based_on_turn=10)
    row = memory.get_summary(conv["id"])
    assert row["summary"] == "first summary"
    assert row["based_on_turn"] == 10

    memory.upsert_summary(conv["id"], "second summary", based_on_turn=20)
    row = memory.get_summary(conv["id"])
    assert row["summary"] == "second summary"
    assert row["based_on_turn"] == 20


# --- Vocab / mistakes ---


def test_upsert_vocab_mistake_creates_then_bumps_occurrences():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.upsert_vocab_mistake(conv["id"], "ser vs estar", note="confused the two")
    memory.upsert_vocab_mistake(conv["id"], "ser vs estar", note="did it again")

    rows = memory.get_vocab_mistakes(conv["id"])
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2
    assert rows[0]["note"] == "did it again"


def test_upsert_vocab_mistake_is_case_insensitive():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.upsert_vocab_mistake(conv["id"], "Hola")
    memory.upsert_vocab_mistake(conv["id"], "hola")

    rows = memory.get_vocab_mistakes(conv["id"])
    assert len(rows) == 1
    assert rows[0]["occurrences"] == 2


def test_upsert_vocab_mistake_ignores_blank_term():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.upsert_vocab_mistake(conv["id"], "   ")
    assert memory.get_vocab_mistakes(conv["id"]) == []


# --- Spaced repetition (record_term_review / get_review_candidates) ---


def test_record_term_review_correct_for_unknown_term_is_a_noop():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.record_term_review(conv["id"], "el clima", correct=True)
    assert memory.get_vocab_mistakes(conv["id"]) == []
    assert memory.get_review_candidates(conv["id"]) == []


def test_record_term_review_incorrect_creates_row_like_upsert_vocab_mistake():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.record_term_review(conv["id"], "el clima", correct=False)

    rows = memory.get_vocab_mistakes(conv["id"])
    assert len(rows) == 1
    assert rows[0]["term"] == "el clima"
    assert rows[0]["occurrences"] == 1
    assert rows[0]["correct_streak"] == 0


def test_missed_term_appears_in_review_candidates():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.record_term_review(conv["id"], "el clima", correct=False)
    assert memory.get_review_candidates(conv["id"]) == ["el clima"]


def test_review_candidates_ordered_by_occurrences_then_recency():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.record_term_review(conv["id"], "el clima", correct=False)
    memory.record_term_review(conv["id"], "sin embargo", correct=False)
    memory.record_term_review(conv["id"], "sin embargo", correct=False)  # missed twice - higher priority

    assert memory.get_review_candidates(conv["id"]) == ["sin embargo", "el clima"]


def test_two_correct_streaks_retires_term_from_review_candidates():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.record_term_review(conv["id"], "el clima", correct=False)
    memory.record_term_review(conv["id"], "el clima", correct=True)
    assert memory.get_review_candidates(conv["id"]) == ["el clima"]  # one clean answer isn't enough yet

    memory.record_term_review(conv["id"], "el clima", correct=True)
    assert memory.get_review_candidates(conv["id"]) == []  # two in a row retires it

    rows = memory.get_vocab_mistakes(conv["id"])
    assert rows[0]["correct_streak"] == 2
    assert rows[0]["occurrences"] == 1  # correct answers never bump occurrences


def test_incorrect_after_streak_resets_it_and_reappears():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.record_term_review(conv["id"], "el clima", correct=False)
    memory.record_term_review(conv["id"], "el clima", correct=True)
    memory.record_term_review(conv["id"], "el clima", correct=True)
    assert memory.get_review_candidates(conv["id"]) == []

    memory.record_term_review(conv["id"], "el clima", correct=False)
    assert memory.get_review_candidates(conv["id"]) == ["el clima"]

    rows = memory.get_vocab_mistakes(conv["id"])
    assert rows[0]["correct_streak"] == 0
    assert rows[0]["occurrences"] == 2


def test_review_candidates_respects_limit():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    for term in ["uno", "dos", "tres", "cuatro"]:
        memory.record_term_review(conv["id"], term, correct=False)
    assert len(memory.get_review_candidates(conv["id"], limit=2)) == 2


def test_init_db_migration_is_idempotent():
    # isolated_data_dir already calls init_db() once; calling it again here
    # exercises the ALTER TABLE ADD COLUMN path against a table that
    # already has the column, which must not raise.
    memory.init_db()
    memory.init_db()


# --- Lesson log ---


def test_append_and_get_lesson_log():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.append_lesson_log(conv["id"], "covered greetings")
    memory.append_lesson_log(conv["id"], "covered numbers 1-10")

    rows = memory.get_lesson_log(conv["id"])
    assert len(rows) == 2
    assert {r["summary"] for r in rows} == {"covered greetings", "covered numbers 1-10"}


def test_append_lesson_log_ignores_blank_note():
    conv = memory.create_conversation("profile-1", {"target_language": "Spanish"})
    memory.append_lesson_log(conv["id"], "  ")
    assert memory.get_lesson_log(conv["id"]) == []
