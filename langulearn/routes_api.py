"""Reference-data endpoints (voices/models/avatars) + the profile and
conversation REST CRUD API. Split out of main.py.
"""

import asyncio
import base64
import os
import re
import subprocess
import sys
import uuid
from importlib import metadata as importlib_metadata
from urllib.parse import quote

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from . import memory, scenarios, speech_detection
from .constants import (
    APP_VERSION,
    ASSETS_DIR,
    DATA_DIR,
    DEFAULT_DIFFICULTY,
    DEFAULT_MIC_CALIBRATION_KEY,
    DEFAULT_MODEL,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_VOICE,
    ENROLLMENT_SENTENCES,
    MODEL_OPTIONS,
    PROFILE_EDITABLE_FIELDS,
    SPEAKER_VERIFICATION_BACKEND,
    THRESHOLD_TEST_NOISE_PROMPT,
    THRESHOLD_TEST_SENTENCES,
    VOICE_OPTIONS,
)
from .export_docx import build_notes_docx
from .profiles_store import (
    delete_profile,
    get_profile_by_id,
    load_profiles,
    patch_profile,
    save_profiles,
)

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
    return {
        "scenarios": scenarios.SCENARIO_OPTIONS,
        "default": scenarios.DEFAULT_SCENARIO,
    }


@router.get("/api/app-info")
def get_app_info():
    """Backs the Settings modal's About tab (see settings.js). Prefers the
    INSTALLED package's own metadata (what pip/importlib actually knows,
    derived from constants.APP_VERSION at build time - see pyproject.toml's
    [tool.setuptools.dynamic] section) over importing APP_VERSION directly,
    since that's the more robust "what version is this actually running"
    check. Falls back to the constant only if the package was never really
    installed at all (e.g. running straight from a source checkout).
    """
    try:
        version = importlib_metadata.version("langulearn")
    except importlib_metadata.PackageNotFoundError:
        version = APP_VERSION
    return {
        "version": version,
        "credits": ["Gemini Live API", "Resemblyzer", "TalkingHead"],
    }


@router.get("/api/avatars")
def get_available_avatars():
    """Which voices currently have a real 3D avatar file, for the
    avatar-select page to gate which grid tiles are clickable vs
    "Coming soon" - avatars are being made one at a time, so this list
    grows over time without any code change needed. Reads from ASSETS_DIR
    (see constants.py), not the package's own bundled static/ - avatar
    .glb files are downloaded separately (see cli.py), not shipped in the
    pip package.
    """
    avatar_dir = ASSETS_DIR / "avatar"
    if not avatar_dir.is_dir():
        return {"available": []}
    available = sorted(p.name[: -len("_th.glb")] for p in avatar_dir.glob("*_th.glb"))
    return {"available": available}


@router.post("/api/open-data-folder")
def open_data_folder():
    """Opens the app's data directory (profiles, conversations, voice
    enrollment, downloaded assets) in the OS file explorer.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = str(DATA_DIR)
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.run(["open", path], check=False)
    else:
        subprocess.run(["xdg-open", path], check=False)
    return {"opened": True, "path": path}


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
        # Starting point for the difficulty toggle next time this profile
        # adds a new language (see avatarSelect.js) - editable from the
        # Settings modal's Learning tab (settings.js).
        "default_difficulty": DEFAULT_DIFFICULTY,
        "active_conversation_id": None,
        "resumption_handle": None,
        "resumption_config": None,
        # Keyed by mic label (see constants.DEFAULT_MIC_CALIBRATION_KEY for
        # the default-mic sentinel) - each entry:
        # {"silence_rms_threshold": float, "speaker_threshold": float,
        #  "calibrated": bool, "tested": bool}. Per-mic so switching mics
        # never overwrites another mic's calibration - see handsfreeSetup.js.
        "mic_calibrations": {},
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
    speech_detection.delete_enrollment(profile_id)
    return {"deleted": True}


@router.get("/api/profiles/{profile_id}/mic-status")
def get_mic_status(profile_id: str):
    """One row per mic this profile has ever calibrated - backs the
    Settings modal's Voice & hands-free tab (see settings.js). Calibrated/
    tested come straight off profile.mic_calibrations; enrolled is checked
    separately since it's stored on disk per speech_detection, not on the
    profile.
    """
    profile = get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    calibrations = profile.get("mic_calibrations") or {}
    mics = []
    for mic_key, cal in calibrations.items():
        mics.append(
            {
                "mic_key": mic_key,
                "label": "Default microphone" if mic_key == DEFAULT_MIC_CALIBRATION_KEY else mic_key,
                "calibrated": bool(cal.get("calibrated")),
                "tested": bool(cal.get("tested")),
                "enrolled": speech_detection.has_enrollment(profile_id, mic_key),
            }
        )
    return {"mics": mics}


# --- Voice enrollment (speaker verification for hands-free mode) ---


@router.get("/api/voice-enrollment-sentences")
def get_voice_enrollment_sentences():
    return {"sentences": ENROLLMENT_SENTENCES}


@router.get("/api/speech-detection-status")
def get_speech_detection_status():
    """Lets the frontend gate the Record/threshold-test buttons on whether
    the speaker-verification model has actually finished loading -
    warm_up() (see main.py's startup hook) can take a real amount of time
    on a machine's very first run (downloading model weights), and without
    this a click during that window would just hang or fail with no
    explanation.
    """
    return {
        "backend": SPEAKER_VERIFICATION_BACKEND,
        "status": speech_detection.get_status(),
    }


@router.get("/api/profiles/{profile_id}/voice-enrollment")
def get_voice_enrollment_status(profile_id: str, mic_key: str):
    if get_profile_by_id(profile_id) is None:
        raise HTTPException(404, "Profile not found")
    return {"enrolled": speech_detection.has_enrollment(profile_id, mic_key)}


@router.post("/api/profiles/{profile_id}/voice-enrollment")
async def submit_voice_enrollment(profile_id: str, request: Request):
    if get_profile_by_id(profile_id) is None:
        raise HTTPException(404, "Profile not found")
    payload = await request.json()
    mic_key = payload.get("mic_key")
    if not mic_key:
        raise HTTPException(400, "mic_key is required.")
    clips_b64 = payload.get("samples") or []
    if not clips_b64:
        raise HTTPException(400, "At least one recorded sample is required.")

    samples = []
    for clip in clips_b64:
        pcm_bytes = base64.b64decode(clip)
        int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
        samples.append(int16.astype(np.float32) / 32768.0)

    try:
        # Embedding is CPU/torch work - run off the event loop so it
        # doesn't block other requests (and any open Live sessions) while
        # it runs, same reasoning as summarize_conversation's own
        # asyncio.to_thread usage in live_session.py.
        await asyncio.to_thread(speech_detection.enroll_profile, profile_id, mic_key, samples)
    except (OSError, RuntimeError, ValueError) as e:
        raise HTTPException(500, f"Voice enrollment failed: {type(e).__name__}: {e}")
    return {"enrolled": True}


@router.delete("/api/profiles/{profile_id}/voice-enrollment")
def remove_voice_enrollment(profile_id: str, mic_key: str):
    if get_profile_by_id(profile_id) is None:
        raise HTTPException(404, "Profile not found")
    speech_detection.delete_enrollment(profile_id, mic_key)
    return {"enrolled": False}


# --- Threshold-calibration test (hands-free-setup page) ---


@router.get("/api/threshold-test-sentences")
def get_threshold_test_sentences():
    return {
        "sentences": THRESHOLD_TEST_SENTENCES,
        "noise_prompt": THRESHOLD_TEST_NOISE_PROMPT,
    }


@router.post("/api/profiles/{profile_id}/voice-enrollment-test")
async def test_voice_enrollment_sample(profile_id: str, request: Request):
    """Scores one recorded clip against this mic's enrolled reference - no
    pass/fail threshold applied here, just the raw similarity number. Used
    both for the 5 THRESHOLD_TEST_SENTENCES readings and for the one
    background-noise capture the hands-free-setup page also collects -
    scoring doesn't care what the clip actually contains, it just measures
    similarity to the reference either way.
    """
    if get_profile_by_id(profile_id) is None:
        raise HTTPException(404, "Profile not found")

    payload = await request.json()
    mic_key = payload.get("mic_key")
    if not mic_key:
        raise HTTPException(400, "mic_key is required.")
    if not speech_detection.has_enrollment(profile_id, mic_key):
        raise HTTPException(400, "This profile hasn't completed voice enrollment for this mic yet.")

    clip_b64 = payload.get("sample")
    if not clip_b64:
        raise HTTPException(400, "A recorded sample is required.")

    pcm_bytes = base64.b64decode(clip_b64)
    int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    audio = int16.astype(np.float32) / 32768.0

    try:
        score = await asyncio.to_thread(speech_detection.score, profile_id, mic_key, audio)
    except (OSError, RuntimeError, ValueError) as e:
        raise HTTPException(500, f"Scoring failed: {type(e).__name__}: {e}")
    return {"score": score}


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
        "difficulty": payload.get("difficulty") or profile.get("default_difficulty") or DEFAULT_DIFFICULTY,
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
    tutor_name = next(
        (v.get("alias") or v["name"] for v in VOICE_OPTIONS if v["name"] == voice_name),
        voice_name,
    )
    return {
        **conv,
        "turns": turns,
        "summary": summary["summary"] if summary else None,
        "tutor_name": tutor_name,
    }


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
    if any(
        k in payload
        for k in (
            "voice_name",
            "native_language",
            "target_language",
            "model_name",
            "difficulty",
        )
    ):
        config = dict(conv["config"])
        for k in (
            "voice_name",
            "native_language",
            "target_language",
            "model_name",
            "difficulty",
        ):
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
        patch_profile(
            profile_id,
            {"active_conversation_id": remaining[0]["id"] if remaining else None},
        )
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


@router.get("/api/profiles/{profile_id}/conversations/{conversation_id}/notes/export.docx")
def export_conversation_notes_docx(profile_id: str, conversation_id: str):
    """Word export of the same content the notes modal shows - see
    export_docx.py. Returned as a plain attachment download.
    """
    profile = get_profile_by_id(profile_id)
    conv = memory.get_conversation(conversation_id)
    if profile is None or conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")

    language_label = conv.get("name") or (conv.get("config") or {}).get("target_language") or "Conversation"
    docx_bytes = build_notes_docx(
        profile.get("name") or "",
        language_label,
        memory.get_vocab_mistakes(conversation_id),
        memory.get_lesson_log(conversation_id),
    )
    display_name = f"{language_label} - Learnt Notes.docx".replace("/", "-")
    # Conversation names routinely contain non-ASCII characters (every
    # auto-named conversation has a middle dot - see
    # memory.default_conversation_name), and HTTP header values are Latin-1,
    # not UTF-8 - passing one straight into Content-Disposition produces a
    # byte an HTTP client can't safely decode back as UTF-8 (caught by the
    # test suite as a UnicodeDecodeError, but the real failure mode for an
    # actual user is a broken/garbled downloaded filename). RFC 6266 covers
    # exactly this: an ASCII-only `filename` fallback plus a percent-encoded
    # `filename*` for clients that support it - every modern browser does.
    ascii_fallback = re.sub(r"[^\x20-\x7e]", "_", display_name)
    headers = {
        "Content-Disposition": f"attachment; filename=\"{ascii_fallback}\"; filename*=UTF-8''{quote(display_name)}",
    }
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )


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
