"""Reference-data endpoints (voices/models/avatars) + the profile and
conversation REST CRUD API. Split out of main.py.
"""

import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from . import memory, scenarios
from .constants import (
    DEFAULT_DIFFICULTY,
    DEFAULT_MODEL,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_VOICE,
    MODEL_OPTIONS,
    PROFILE_EDITABLE_FIELDS,
    VOICE_OPTIONS,
)
from .profiles_store import delete_profile, get_profile_by_id, load_profiles, patch_profile, save_profiles

router = APIRouter()


# --- Reference data endpoints ---

@router.get("/api/voices")
def get_voices():
    return {"voices": VOICE_OPTIONS, "default": DEFAULT_VOICE}


@router.get("/api/models")
def get_models():
    return {"models": MODEL_OPTIONS, "default": DEFAULT_MODEL}


@router.get("/api/scenarios")
def get_scenarios():
    return {"scenarios": scenarios.SCENARIO_OPTIONS, "default": scenarios.DEFAULT_SCENARIO}


@router.get("/api/avatars")
def get_available_avatars():
    """Which voices currently have a real 3D avatar file, for the
    avatar-select page to gate which grid tiles are clickable vs
    "Coming soon" - avatars are being made one at a time (see the .glb
    files under static/avatar/), so this list grows over time without any
    code change needed.
    """
    avatar_dir = Path("static/avatar")
    if not avatar_dir.is_dir():
        return {"available": []}
    available = sorted(p.name[:-len("_th.glb")] for p in avatar_dir.glob("*_th.glb"))
    return {"available": available}


# --- Profiles REST API ---

@router.get("/api/profiles")
def list_profiles():
    return {"profiles": [{"id": p["id"], "name": p["name"]} for p in load_profiles()]}


@router.post("/api/profiles")
async def create_profile(request: Request):
    payload = await request.json()
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    api_key = (payload.get("api_key") or "").strip() or None

    profile = {
        "id": str(uuid.uuid4()),
        "name": name,
        "api_key": api_key,
        "mic_device_id": None,
        "mic_label": None,
        "voice_name": DEFAULT_VOICE,
        "voice_gender": next((v["gender"] for v in VOICE_OPTIONS if v["name"] == DEFAULT_VOICE), "Female"),
        "native_language": DEFAULT_NATIVE_LANGUAGE,
        "target_language": DEFAULT_TARGET_LANGUAGE,
        "model_name": DEFAULT_MODEL,
        "active_conversation_id": None,
        "resumption_handle": None,
        "resumption_config": None,
    }
    profiles = load_profiles()
    profiles.append(profile)
    save_profiles(profiles)
    return profile


@router.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str):
    profile = get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    return profile


@router.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: str, request: Request):
    payload = await request.json()
    fields = {k: v for k, v in payload.items() if k in PROFILE_EDITABLE_FIELDS}
    updated = patch_profile(profile_id, fields)
    if updated is None:
        raise HTTPException(404, "Profile not found")
    return updated


@router.delete("/api/profiles/{profile_id}")
def remove_profile(profile_id: str):
    if not delete_profile(profile_id):
        raise HTTPException(404, "Profile not found")
    for conv in memory.list_conversations(profile_id):
        memory.delete_conversation(conv["id"])
    return {"deleted": True}


# --- Conversations REST API (per profile) ---

@router.get("/api/profiles/{profile_id}/conversations")
def list_conversations_endpoint(profile_id: str):
    profile = get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    return {
        "conversations": memory.list_conversations(profile_id),
        "active_conversation_id": profile.get("active_conversation_id"),
    }


@router.post("/api/profiles/{profile_id}/conversations")
async def create_conversation_endpoint(profile_id: str, request: Request):
    profile = get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    payload = await request.json()
    config = {
        "voice_name": payload.get("voice_name") or profile.get("voice_name") or DEFAULT_VOICE,
        "native_language": payload.get("native_language") or profile.get("native_language") or DEFAULT_NATIVE_LANGUAGE,
        "target_language": payload.get("target_language") or profile.get("target_language") or DEFAULT_TARGET_LANGUAGE,
        "model_name": payload.get("model_name") or profile.get("model_name") or DEFAULT_MODEL,
        "scenario": payload.get("scenario") or scenarios.DEFAULT_SCENARIO,
        "difficulty": payload.get("difficulty") or DEFAULT_DIFFICULTY,
    }
    name = (payload.get("name") or "").strip() or None
    conv = memory.create_conversation(profile_id, config, name=name)
    patch_profile(profile_id, {"active_conversation_id": conv["id"]})
    return conv


@router.get("/api/profiles/{profile_id}/conversations/{conversation_id}")
def get_conversation_endpoint(profile_id: str, conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")
    turns = memory.get_turns(conversation_id)
    summary = memory.get_summary(conversation_id)
    voice_name = (conv.get("config") or {}).get("voice_name") or DEFAULT_VOICE
    tutor_name = next((v.get("alias") or v["name"] for v in VOICE_OPTIONS if v["name"] == voice_name), voice_name)
    return {**conv, "turns": turns, "summary": summary["summary"] if summary else None, "tutor_name": tutor_name}


@router.put("/api/profiles/{profile_id}/conversations/{conversation_id}")
async def update_conversation_endpoint(profile_id: str, conversation_id: str, request: Request):
    conv = memory.get_conversation(conversation_id)
    if conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")
    payload = await request.json()

    name = payload.get("name")
    if isinstance(name, str):
        name = name.strip() or None

    config = None
    if any(k in payload for k in ("voice_name", "native_language", "target_language", "model_name")):
        config = dict(conv["config"])
        for k in ("voice_name", "native_language", "target_language", "model_name"):
            if payload.get(k):
                config[k] = payload[k]

    updated = memory.update_conversation(conversation_id, name=name, config=config)
    return updated


@router.delete("/api/profiles/{profile_id}/conversations/{conversation_id}")
def delete_conversation_endpoint(profile_id: str, conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")
    memory.delete_conversation(conversation_id)

    profile = get_profile_by_id(profile_id)
    if profile and profile.get("active_conversation_id") == conversation_id:
        remaining = memory.list_conversations(profile_id)
        patch_profile(profile_id, {"active_conversation_id": remaining[0]["id"] if remaining else None})
    return {"deleted": True}


@router.get("/api/profiles/{profile_id}/conversations/{conversation_id}/notes")
def get_conversation_notes_endpoint(profile_id: str, conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")
    return {
        "vocab_mistakes": memory.get_vocab_mistakes(conversation_id),
        "lesson_log": memory.get_lesson_log(conversation_id),
    }


@router.put("/api/profiles/{profile_id}/active-conversation")
async def set_active_conversation_endpoint(profile_id: str, request: Request):
    profile = get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    payload = await request.json()
    conversation_id = payload.get("conversation_id")
    conv = memory.get_conversation(conversation_id) if conversation_id else None
    if conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")
    patch_profile(profile_id, {"active_conversation_id": conversation_id})
    return {"active_conversation_id": conversation_id}
