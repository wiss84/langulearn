"""Unit/integration tests for profiles_store.py - the profiles.json layer
and the Gemini client factory. Note: delete_profile here only removes the
profile row itself; cascading its conversations/voice-enrollment data is
routes_api.py's job (remove_profile endpoint) - covered in
test_routes_api.py, not here.
"""

import pytest

from thirtytutors import profiles_store

pytestmark = pytest.mark.integration


def test_load_profiles_returns_empty_list_when_file_missing():
    assert profiles_store.load_profiles() == []


def test_save_then_load_round_trips():
    profiles = [{"id": "a", "name": "Alice"}, {"id": "b", "name": "Bob"}]
    profiles_store.save_profiles(profiles)
    assert profiles_store.load_profiles() == profiles


def test_load_profiles_returns_empty_list_on_corrupt_json(tmp_path):
    profiles_store.PROFILES_FILE.parent.mkdir(parents=True, exist_ok=True)
    profiles_store.PROFILES_FILE.write_text("{not valid json", encoding="utf-8")
    assert profiles_store.load_profiles() == []


def test_get_profile_by_id_found_and_missing():
    profiles_store.save_profiles([{"id": "a", "name": "Alice"}])
    assert profiles_store.get_profile_by_id("a")["name"] == "Alice"
    assert profiles_store.get_profile_by_id("nope") is None


def test_patch_profile_merges_fields_and_persists():
    profiles_store.save_profiles([{"id": "a", "name": "Alice", "native_language": "English"}])
    updated = profiles_store.patch_profile("a", {"native_language": "Spanish"})
    assert updated["native_language"] == "Spanish"
    assert updated["name"] == "Alice"  # untouched field survives the merge
    # And it's actually persisted, not just returned:
    assert profiles_store.get_profile_by_id("a")["native_language"] == "Spanish"


def test_patch_profile_returns_none_for_unknown_id():
    profiles_store.save_profiles([{"id": "a", "name": "Alice"}])
    assert profiles_store.patch_profile("nope", {"name": "x"}) is None


def test_delete_profile_removes_only_that_profile():
    profiles_store.save_profiles([{"id": "a", "name": "Alice"}, {"id": "b", "name": "Bob"}])
    assert profiles_store.delete_profile("a") is True
    remaining = profiles_store.load_profiles()
    assert [p["id"] for p in remaining] == ["b"]


def test_delete_profile_returns_false_for_unknown_id():
    profiles_store.save_profiles([{"id": "a", "name": "Alice"}])
    assert profiles_store.delete_profile("nope") is False


# --- get_client_for_key ---


def test_get_client_for_key_raises_without_a_key():
    with pytest.raises(ValueError):
        profiles_store.get_client_for_key(None)
    with pytest.raises(ValueError):
        profiles_store.get_client_for_key("   ")


def test_get_client_for_key_builds_a_client_given_a_key():
    client = profiles_store.get_client_for_key("fake-test-key")
    assert client is not None
