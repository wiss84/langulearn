"""Profile JSON storage (data/profiles.json) + the Gemini client factory
built from a profile's own API key. Split out of main.py so route modules
and live_session.py can share this without importing each other.
"""

import json

from google import genai

from .constants import DATA_DIR, PROFILES_FILE


def load_profiles() -> list[dict]:
    if not PROFILES_FILE.exists():
        return []
    try:
        return json.loads(PROFILES_FILE.read_text(encoding="utf-8")).get("profiles", [])
    except (json.JSONDecodeError, OSError):
        return []


def save_profiles(profiles: list[dict]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROFILES_FILE.write_text(json.dumps({"profiles": profiles}, indent=2), encoding="utf-8")


def get_profile_by_id(profile_id: str) -> dict | None:
    for p in load_profiles():
        if p["id"] == profile_id:
            return p
    return None


def patch_profile(profile_id: str, fields: dict) -> dict | None:
    profiles = load_profiles()
    for p in profiles:
        if p["id"] == profile_id:
            p.update(fields)
            save_profiles(profiles)
            return p
    return None


def delete_profile(profile_id: str) -> bool:
    profiles = load_profiles()
    for i, p in enumerate(profiles):
        if p["id"] == profile_id:
            profiles.pop(i)
            save_profiles(profiles)
            return True
    return False


def get_client_for_key(api_key: str | None) -> genai.Client:
    """Builds a Gemini client from a profile's own API key. Each profile
    carries its own key (see constants.PROFILE_EDITABLE_FIELDS) - there's
    no shared or .env fallback, so a profile without one simply can't open
    a session or run summarization; callers should catch ValueError and
    surface it.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("No Gemini API key is set for this profile.")
    return genai.Client(api_key=api_key)
