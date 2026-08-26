"""Integration tests for the profile export/import (backup) functions in
memory.py, quizzes.py, and profiles_store.py. Uses the real SQLite file and
real filesystem (isolated per test via conftest.py's isolated_data_dir),
covering: export only pulling one profile's data, round-tripping every
nested table (turns, summary, vocab_mistakes, lesson_log, quiz sessions/
items), import replacing rather than merging on re-import, and a second
profile's data staying completely untouched throughout.
"""

import numpy as np
import pytest

from thirtytutors import memory, profiles_store, quizzes
from thirtytutors.speech_detection import enrollment

pytestmark = pytest.mark.integration


def _add_full_conversation_content(conv_id: str):
    """Populates one conversation with one row in every table export needs
    to cover, so a single conversation is enough to exercise the whole
    round-trip.
    """
    memory.insert_turn(conv_id, "user", "Hola, \u00bfc\u00f3mo est\u00e1s?")
    memory.insert_turn(conv_id, "tutor", "\u00a1Muy bien, gracias!")
    memory.upsert_summary(conv_id, "Practiced greetings.", based_on_turn=2)
    memory.upsert_vocab_mistake(conv_id, "ser vs estar", note="confused the two")
    memory.append_lesson_log(conv_id, "covered greetings")

    quiz_id = quizzes.start_quiz_session(
        conv_id,
        "multiple_choice",
        {
            "intro_message": "Quick check",
            "items": [{"target_term": "hola", "prompt": "Say hello", "choices": ["hola", "adios"], "correct_choice_index": 0}],
        },
    )
    quizzes.record_item_answer(quiz_id, 0, "hola", "Say hello", "hola", "hola", True)
    quizzes.finalize_quiz_session(quiz_id, "completed")
    return quiz_id


# --- Export scoping ---


def test_export_only_includes_this_profiles_conversations(make_profile, make_conversation):
    profile_a = make_profile(name="Alice", target_language="Spanish")
    profile_b = make_profile(name="Bob", target_language="French")
    conv_a = make_conversation(profile_a["id"], target_language="Spanish")
    make_conversation(profile_b["id"], target_language="French")

    exported = memory.export_profile_conversations(profile_a["id"])

    assert len(exported) == 1
    assert exported[0]["id"] == conv_a["id"]
    assert exported[0]["profile_id"] == profile_a["id"]


def test_export_of_profile_with_no_conversations_returns_empty_list(make_profile):
    profile = make_profile(name="Empty")
    assert memory.export_profile_conversations(profile["id"]) == []


# --- Export completeness ---


def test_export_includes_every_nested_table(make_profile, make_conversation):
    profile = make_profile(name="Alice", target_language="Spanish")
    conv = make_conversation(profile["id"], target_language="Spanish")
    _add_full_conversation_content(conv["id"])

    [exported] = memory.export_profile_conversations(profile["id"])

    assert len(exported["turns"]) == 2
    assert exported["turns"][0]["text"] == "Hola, \u00bfc\u00f3mo est\u00e1s?"
    assert exported["summary"]["summary"] == "Practiced greetings."
    assert len(exported["vocab_mistakes"]) == 1
    assert exported["vocab_mistakes"][0]["term"] == "ser vs estar"
    assert len(exported["lesson_log"]) == 1
    assert len(exported["quiz_sessions"]) == 1
    assert exported["quiz_sessions"][0]["status"] == "completed"
    assert len(exported["quiz_sessions"][0]["items"]) == 1
    assert exported["quiz_sessions"][0]["items"][0]["target_term"] == "hola"


# --- Import round-trip ---


def test_import_restores_a_fully_deleted_conversation(make_profile, make_conversation):
    profile = make_profile(name="Alice", target_language="Spanish")
    conv = make_conversation(profile["id"], target_language="Spanish")
    _add_full_conversation_content(conv["id"])

    exported = memory.export_profile_conversations(profile["id"])
    memory.delete_conversation(conv["id"])
    assert memory.get_conversation(conv["id"]) is None

    memory.import_profile_conversations(profile["id"], exported)

    restored = memory.get_conversation(conv["id"])
    assert restored is not None
    assert restored["config"]["target_language"] == "Spanish"
    assert len(memory.get_turns(conv["id"])) == 2
    assert memory.get_summary(conv["id"])["summary"] == "Practiced greetings."
    assert len(memory.get_vocab_mistakes(conv["id"])) == 1
    assert len(memory.get_lesson_log(conv["id"])) == 1

    sessions = quizzes.get_quiz_sessions(conv["id"])
    assert len(sessions) == 1
    assert sessions[0]["status"] == "completed"
    assert len(quizzes.get_quiz_items(sessions[0]["quiz_id"])) == 1


def test_reimporting_the_same_export_does_not_duplicate_rows(make_profile, make_conversation):
    profile = make_profile(name="Alice", target_language="Spanish")
    conv = make_conversation(profile["id"], target_language="Spanish")
    _add_full_conversation_content(conv["id"])
    exported = memory.export_profile_conversations(profile["id"])

    memory.import_profile_conversations(profile["id"], exported)
    memory.import_profile_conversations(profile["id"], exported)

    assert len(memory.get_turns(conv["id"])) == 2
    assert len(memory.get_vocab_mistakes(conv["id"])) == 1
    assert len(memory.get_lesson_log(conv["id"])) == 1
    assert len(quizzes.get_quiz_sessions(conv["id"])) == 1


def test_import_overwrites_local_changes_made_after_export(make_profile, make_conversation):
    profile = make_profile(name="Alice", target_language="Spanish")
    conv = make_conversation(profile["id"], target_language="Spanish")
    _add_full_conversation_content(conv["id"])
    exported = memory.export_profile_conversations(profile["id"])

    # Simulate local changes made after the backup was taken.
    memory.insert_turn(conv["id"], "user", "a turn added after the backup")
    memory.upsert_vocab_mistake(conv["id"], "a mistake added after the backup")

    memory.import_profile_conversations(profile["id"], exported)

    turns = memory.get_turns(conv["id"])
    assert len(turns) == 2  # back to exactly what was in the backup
    assert all(t["text"] != "a turn added after the backup" for t in turns)
    mistakes = memory.get_vocab_mistakes(conv["id"])
    assert len(mistakes) == 1
    assert mistakes[0]["term"] == "ser vs estar"


def test_import_does_not_touch_a_different_profiles_data(make_profile, make_conversation):
    profile_a = make_profile(name="Alice", target_language="Spanish")
    profile_b = make_profile(name="Bob", target_language="French")
    conv_a = make_conversation(profile_a["id"], target_language="Spanish")
    conv_b = make_conversation(profile_b["id"], target_language="French")
    _add_full_conversation_content(conv_a["id"])
    memory.insert_turn(conv_b["id"], "user", "bonjour")

    exported_a = memory.export_profile_conversations(profile_a["id"])
    memory.delete_conversation(conv_a["id"])
    memory.import_profile_conversations(profile_a["id"], exported_a)

    b_turns = memory.get_turns(conv_b["id"])
    assert len(b_turns) == 1
    assert b_turns[0]["text"] == "bonjour"


# --- profiles_store.upsert_profile ---


def test_upsert_profile_appends_when_new():
    profiles_store.upsert_profile({"id": "p1", "name": "Alice"})
    assert profiles_store.get_profile_by_id("p1")["name"] == "Alice"


def test_upsert_profile_replaces_when_id_already_exists():
    profiles_store.upsert_profile({"id": "p1", "name": "Alice", "native_language": "English"})
    profiles_store.upsert_profile({"id": "p1", "name": "Alice V2"})

    profiles = profiles_store.load_profiles()
    assert len(profiles) == 1
    assert profiles[0]["name"] == "Alice V2"
    assert "native_language" not in profiles[0]  # a full replace, not a merge - see upsert_profile's docstring


# --- Voice enrollment folder (plain filesystem copy, no ML backend needed) ---


def test_profile_enrollment_dir_round_trip_via_plain_copy(make_profile, tmp_path):
    profile = make_profile(name="Alice")
    dummy_reference = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    mic_dir = enrollment._mic_dir(profile["id"], "Built-in Microphone")
    mic_dir.mkdir(parents=True)
    np.save(mic_dir / "reference_embedding.npy", dummy_reference)
    (mic_dir / "reference_backend.txt").write_text("resemblyzer", encoding="utf-8")

    source = enrollment.profile_enrollment_dir(profile["id"])
    assert source.is_dir()

    import shutil

    backup_copy = tmp_path / "backed_up_enrollment"
    shutil.copytree(source, backup_copy)
    shutil.rmtree(source)
    assert not source.exists()

    shutil.copytree(backup_copy, source)
    restored = np.load(mic_dir / "reference_embedding.npy")
    np.testing.assert_array_equal(restored, dummy_reference)
