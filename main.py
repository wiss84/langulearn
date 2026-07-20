"""
FastAPI backend: Gemini Live API relay + profile/conversation REST API.

Turn signaling (push-to-talk):
    client sends "init"          -> session config, must be the first WS message
    client sends "start_turn"    -> server sends activity_start to Gemini
    client streams "audio_chunk" -> forwarded as realtime audio input
    client sends "turn_complete" -> server sends activity_end to Gemini

Both sides of the transcript come directly from Gemini's own Live API:
input_audio_transcription for the student's side, output_audio_transcription
for the tutor's - see build_config below. (This app briefly used a separate
local Whisper pipeline for the student's side, hoping for better accuracy
than Gemini's own transcription - see git history / transcribe.py if that's
ever worth revisiting - but on CPU-only hardware it ran roughly 15-20x
slower than real-time, which made the live transcript display unusable
regardless of any UI fix. Reverted back to Gemini's native transcription for
both sides.)

Session memory has two layers now:

1. Within a single open Live API connection, the model retains full
   conversation context automatically - no extra work needed. What breaks
   continuity is a reconnect (changing voice/language/model, hitting the
   session time limit, restarting the app, or switching to a different
   conversation), since each reconnect opens a technically new session on
   Google's side. The Live API's session-resumption handle lets a reconnect
   pick up the same context - it's stored per *conversation* now (see
   memory.py), not per profile, since a profile can hold several
   conversations, each with its own voice/language/model and its own
   resumable session.

2. Because a resumption handle only survives a finite window and dies the
   moment voice/model/language change, it alone isn't durable memory. Every
   transcribed turn is also persisted to SQLite (memory.py) and periodically
   folded into a short rolling summary. When a conversation's session starts
   fresh (no handle, or handle rejected), that summary is injected into the
   system instruction so the tutor still "remembers" earlier turns instead
   of a cold start.

Retry behavior: Gemini's preview models occasionally return a transient
"500 INTERNAL", "503 UNAVAILABLE", or (less often) a 429 rate-limit error
that succeeds if you just try again - see retry.py for the shared
classification/backoff logic. The initial Live session connection retries
up to 4 additional times with exponential backoff (or Google's own
suggested retryDelay when present) before giving up, and the periodic
summarization call (summarize_conversation) retries the same way. Errors
that aren't recognizably transient (bad request, auth, unknown model, etc.)
fail immediately instead of being retried pointlessly.

Run:
    uvicorn main:app --reload --port 8000
Then open:
    http://127.0.0.1:8000/
"""

import asyncio
import base64
import json
import traceback
import uuid
from pathlib import Path

import mimetypes
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# StaticFiles doesn't know the .mjs extension, so it serves ES modules as
# text/plain - which browsers refuse to execute (the "disallowed MIME type"
# error). On some systems mimetypes also mis-resolves .js to application/json,
# so force both to JavaScript.
mimetypes.add_type("text/javascript", ".mjs")
mimetypes.add_type("text/javascript", ".js")

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

import memory
import retry

load_dotenv()

# System instruction template. {name}/{native_language}/{target_language}
# are filled in per conversation, so the same wording works for any language
# pair and any conversation under a profile.
SYSTEM_INSTRUCTION_TEMPLATE = (
    "You are a friendly, conversational {target_language} language tutor helper for {name}, "
    "a {native_language} speaker. "
    "Your primary language of instruction, explanation, and casual chat is {native_language}. "
    "To prevent confusing the student, never reply entirely in {target_language}.\n\n"
    "Follow these behavioral rules based on {name}'s input:\n"
    "1. When {name} speaks in {native_language}: Respond in {native_language} to acknowledge or "
    "explain, and then naturally introduce the {target_language} translation or equivalent. "
    "Explain briefly *why* that particular {target_language} phrase fits best.\n"
    "2. When {name} attempts to speak in {target_language}: Gently correct any grammar, spelling, "
    "or context mistakes in {native_language} first. Explain the correction briefly, then "
    "encourage them to keep going.\n\n"
    "Keep your responses short, interactive, and conversational—like a quick back-and-forth chat, "
    "not a textbook chapter."
)

# Appended to the system instruction only when a conversation is starting a
# fresh Live session (no valid resumption handle) and has a stored rolling
# summary - re-seeds context Google's own session state can no longer carry.
MEMORY_CONTEXT_TEMPLATE = (
    "\n\nContext remembered from earlier conversations with {name} (use this "
    "naturally to stay consistent - don't recite it verbatim or announce that "
    "you're reading notes):\n{summary}"
)

DEFAULT_VOICE = "Kore"
DEFAULT_NATIVE_LANGUAGE = "English"
DEFAULT_TARGET_LANGUAGE = "Polish"

# Non-realtime text model used only for periodic rolling-summary folding
# (memory.py / summarize_conversation below) - cheap free-tier text calls,
# separate from the Live API models used for the actual conversation.
# gemini-2.5-flash's free tier is capped at 20 RPD, too low for a
# summarization call firing every ~15 turns across active conversations;
# gemini-3.1-flash-lite gives 500 RPD instead and is plenty for this task.
SUMMARY_MODEL = "gemini-3.1-flash-lite"

# Official voice names + descriptors are from Google's docs
# (ai.google.dev/gemini-api/docs/speech-generation), which label voices by
# tone/character. Gender is not an official Google label - this mapping is
# a manual categorization for the UI's gender-first picker, not a claim
# Google makes. pitch is a supplementary manual categorization (same
# spirit as gender), shown alongside descriptor on the avatar-select page.
VOICE_OPTIONS = [
    {"name": "Zephyr", "descriptor": "Bright", "gender": "Female", "pitch": "Mid-range"},
    {"name": "Puck", "descriptor": "Upbeat", "gender": "Male", "pitch": "Mid-range"},
    {"name": "Charon", "descriptor": "Informative", "gender": "Male", "pitch": "Mid-to-low"},
    {"name": "Kore", "descriptor": "Firm", "gender": "Female", "pitch": "Mid-to-high"},
    {"name": "Fenrir", "descriptor": "Excitable", "gender": "Male", "pitch": "Mid-range"},
    {"name": "Leda", "descriptor": "Youthful", "gender": "Female", "pitch": "High"},
    {"name": "Orus", "descriptor": "Firm", "gender": "Male", "pitch": "Mid-to-low"},
    {"name": "Aoede", "descriptor": "Breezy", "gender": "Female", "pitch": "Mid-range"},
    {"name": "Callirrhoe", "descriptor": "Easy-going", "gender": "Female", "pitch": "Mid-to-high"},
    {"name": "Autonoe", "descriptor": "Bright", "gender": "Female", "pitch": "High"},
    {"name": "Enceladus", "descriptor": "Breathy", "gender": "Male", "pitch": "Mid-range"},
    {"name": "Iapetus", "descriptor": "Clear", "gender": "Male", "pitch": "Mid-to-low"},
    {"name": "Umbriel", "descriptor": "Easy-going", "gender": "Male", "pitch": "Mid-range"},
    {"name": "Algieba", "descriptor": "Smooth", "gender": "Female", "pitch": "Mid-to-low"},
    {"name": "Despina", "descriptor": "Smooth", "gender": "Female", "pitch": "Mid-range"},
    {"name": "Erinome", "descriptor": "Clear", "gender": "Female", "pitch": "Mid-to-high"},
    {"name": "Algenib", "descriptor": "Gravelly", "gender": "Male", "pitch": "Low"},
    {"name": "Rasalgethi", "descriptor": "Informative", "gender": "Male", "pitch": "Mid-range"},
    {"name": "Laomedeia", "descriptor": "Upbeat", "gender": "Female", "pitch": "Mid-range"},
    {"name": "Achernar", "descriptor": "Soft", "gender": "Female", "pitch": "Mid-range"},
    {"name": "Alnilam", "descriptor": "Firm", "gender": "Male", "pitch": "Mid-to-low"},
    {"name": "Schedar", "descriptor": "Even", "gender": "Male", "pitch": "Mid-to-low"},
    {"name": "Gacrux", "descriptor": "Mature", "gender": "Female", "pitch": "Mid-to-low"},
    {"name": "Pulcherrima", "descriptor": "Forward", "gender": "Female", "pitch": "High"},
    {"name": "Achird", "descriptor": "Friendly", "gender": "Male", "pitch": "Mid-to-high"},
    {"name": "Zubenelgenubi", "descriptor": "Casual", "gender": "Male", "pitch": "Low"},
    {"name": "Vindemiatrix", "descriptor": "Gentle", "gender": "Female", "pitch": "Low"},
    {"name": "Sadachbia", "descriptor": "Lively", "gender": "Male", "pitch": "Low"},
    {"name": "Sadaltager", "descriptor": "Knowledgeable", "gender": "Male", "pitch": "Mid-range"},
    {"name": "Sulafat", "descriptor": "Warm", "gender": "Female", "pitch": "Mid-to-high"},
]

# Live API model choices. Rate limits are what's visible on the free tier as
# of mid-2026 and can change - shown in the UI so the choice is informed.
# Only the 2.5 native-audio generation exposes "affective dialog" (emotional
# tone sensitivity/expression); the 3.x line traded that for lower latency.
MODEL_OPTIONS = [
    {
        "id": "gemini-2.5-flash-native-audio-preview-09-2025",
        "label": "Gemini 2.5 Flash Native Audio Dialog",
        "rate_limit_note": "1M TPM",
        "supports_affective_dialog": True,
    },
    {
        "id": "gemini-3.1-flash-live-preview",
        "label": "Gemini 3 Flash Live",
        "rate_limit_note": "65K TPM",
        "supports_affective_dialog": False,
    },
]
DEFAULT_MODEL = MODEL_OPTIONS[0]["id"]

DATA_DIR = Path(__file__).parent / "data"
PROFILES_FILE = DATA_DIR / "profiles.json"

# voice_name/native_language/target_language/model_name remain here only as
# legacy seed fields from before every conversation was created explicitly
# via /avatar-select - no longer read by anything (see get_active_conversation,
# which no longer auto-creates a conversation from them). resumption_handle/
# resumption_config are similarly unused legacy fields. Kept on new profiles
# only for schema consistency with old data; safe to remove entirely later.
PROFILE_EDITABLE_FIELDS = (
    "mic_device_id", "mic_label", "voice_name", "voice_gender",
    "name", "native_language", "target_language", "model_name",
    "active_conversation_id", "api_key",
)

# Live API connect retries. Classification (is_transient_error) and
# retryDelay parsing live in retry.py, shared with summarize_conversation's
# retry.call_with_retry below - this loop stays separate only because it
# manages an async context manager (see _connect_live_with_retries).
RETRY_ATTEMPTS = 4  # additional attempts after the first, so 5 tries total
RETRY_BASE_DELAY = 1.5  # seconds, doubles each retry (or uses Google's suggested retryDelay if present)


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
    carries its own key (see PROFILE_EDITABLE_FIELDS) - there's no shared
    or .env fallback, so a profile without one simply can't open a session
    or run summarization; callers should catch ValueError and surface it.
    """
    api_key = (api_key or "").strip()
    if not api_key:
        raise ValueError("No Gemini API key is set for this profile.")
    return genai.Client(api_key=api_key)


memory.init_db()
app = FastAPI()

# Frontend is composed via Jinja2 from static/UI (index.html layout +
# pages/ + styles/ + scripts/) instead of one flat index.html/app.js/
# style.css, so pages/styles/scripts stay short and descriptively named
# rather than growing into large monolithic files. Static assets under
# static/UI/styles and static/UI/scripts, plus vendor/, avatar/, and
# voices/, are still served as plain files by the StaticFiles mount at the
# bottom of this module - only the page shell itself is server-rendered.
templates = Jinja2Templates(directory="static/UI")


@app.middleware("http")
async def _disable_static_caching(request: Request, call_next):
    """This app is only ever served to one local desktop window (see
    desktop.py), not a CDN-fronted public site, so there's no upside to
    browser caching of the frontend - only downside. desktop.py
    deliberately keeps a persistent WebView2 profile across launches
    (private_mode=False, so the microphone permission prompt doesn't
    reappear every restart), and that same persistence meant the browser
    cache also survived restarts - which once served a stale index.html
    (missing newly-added markup) with no request even hitting this server,
    no error, just a silently blank feature. Marking every non-API,
    non-websocket response as non-cacheable removes that whole class of
    "why isn't my change showing up" bugs going forward.
    """
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and path != "/ws/session":
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def get_active_conversation(profile: dict) -> dict | None:
    """Returns the profile's active conversation, or None if it has none
    yet. No auto-creation - every conversation now comes from an explicit
    choice on /avatar-select (an avatar/voice and a target language the
    user picked), so a profile with none just means the user hasn't
    started a language yet, not something to paper over with a synthetic
    "Default" conversation.
    """
    profile_id = profile["id"]
    convs = memory.list_conversations(profile_id)
    if not convs:
        return None
    active_id = profile.get("active_conversation_id")
    match = next((c for c in convs if c["id"] == active_id), None)
    return match or convs[0]


def summarize_conversation(conversation_id: str, student_name: str, api_key: str | None) -> None:
    """Folds turns since the last summary into an updated rolling summary,
    and - from the same call, since it's already looking at the transcript -
    upserts any vocabulary/recurring-mistake terms into vocab_mistakes and
    appends one lesson_log line for this batch of turns.

    Best-effort throughout: any failure here is swallowed and just logged -
    the raw turns stay in SQLite either way, so nothing is lost, and the
    next due summarization attempt (or the final one on disconnect) can
    retry. A malformed/non-JSON model response degrades to "summary only"
    (see the parsing fallback below) rather than losing the summary too.
    A profile with no API key set just skips this - the raw turns are still
    safe in SQLite for whenever a key gets added.
    """
    try:
        client = get_client_for_key(api_key)
    except ValueError:
        print(f"[summarize_conversation] skipped for conversation={conversation_id!r}: no API key set for this profile.")
        return

    try:
        prev = memory.get_summary(conversation_id)
        since_seq = prev["based_on_turn"] if prev else 0
        new_turns = memory.get_turns(conversation_id, since_seq=since_seq)
        if not new_turns:
            return
        transcript_lines = "\n".join(f"{t['role']}: {t['text']}" for t in new_turns)
        prompt = (
            "You maintain memory for an ongoing language-tutoring conversation "
            f"with {student_name}. Given the previous rolling summary and the "
            "new turns below, respond with ONLY a JSON object (no markdown "
            "fences, no commentary) shaped exactly like this:\n"
            '{"summary": "...", "vocab": [{"term": "...", "note": "..."}], "lesson_note": "..."}\n\n'
            "- summary: the previous summary updated to fold in the new turns. "
            "Compact (roughly 150-250 words), plain prose notes (not a "
            "transcript, not addressed to anyone): topics covered, recurring "
            "mistakes, vocabulary introduced, open threads to follow up on.\n"
            "- vocab: 0-8 notable vocabulary words/phrases or recurring "
            "mistakes FROM THIS BATCH ONLY (not the whole summary). term is "
            "the word/phrase itself; note is a few words of context (meaning, "
            "or what correction it needed). Omit if nothing notable came up.\n"
            "- lesson_note: one short sentence describing what this batch of "
            "turns covered - phrased as a log entry, e.g. 'Practiced ordering "
            "food at a restaurant, worked on past tense.'\n\n"
            f"PREVIOUS SUMMARY:\n{prev['summary'] if prev else '(none yet - this is the first summary)'}\n\n"
            f"NEW TURNS:\n{transcript_lines}"
        )
        response = retry.call_with_retry(
            client.models.generate_content,
            model=SUMMARY_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_mime_type="application/json"),
            label=f"summarize_conversation/{conversation_id}",
        )
        raw_text = (getattr(response, "text", None) or "").strip()
        if not raw_text:
            return

        summary_text = None
        vocab_items = []
        lesson_note = None
        try:
            parsed = json.loads(raw_text)
            summary_text = (parsed.get("summary") or "").strip() or None
            vocab_items = parsed.get("vocab") or []
            lesson_note = (parsed.get("lesson_note") or "").strip() or None
        except (json.JSONDecodeError, AttributeError):
            # Model didn't follow the JSON contract - fall back to treating
            # the whole response as the summary, so at least that half of
            # the memory update still lands.
            summary_text = raw_text

        if summary_text:
            memory.upsert_summary(conversation_id, summary_text, new_turns[-1]["seq"])
        for item in vocab_items:
            if isinstance(item, dict) and item.get("term"):
                memory.upsert_vocab_mistake(conversation_id, str(item["term"]), item.get("note"))
        if lesson_note:
            memory.append_lesson_log(conversation_id, lesson_note)

        if summary_text:
            print(f"[summarize_conversation] updated summary for conversation={conversation_id!r} (through turn {new_turns[-1]['seq']}, +{len(vocab_items)} vocab items)")
    except Exception as e:
        print(f"[summarize_conversation] skipped for conversation={conversation_id!r}: {type(e).__name__}: {e}")
        traceback.print_exc()


def build_config(
    profile: dict,
    conv_config: dict,
    model_name: str,
    resumption_handle: str | None = None,
    summary_text: str | None = None,
) -> types.LiveConnectConfig:
    system_instruction = SYSTEM_INSTRUCTION_TEMPLATE.format(
        name=profile.get("name") or "the student",
        native_language=conv_config.get("native_language") or DEFAULT_NATIVE_LANGUAGE,
        target_language=conv_config.get("target_language") or DEFAULT_TARGET_LANGUAGE,
    )
    if summary_text:
        system_instruction += MEMORY_CONTEXT_TEMPLATE.format(
            name=profile.get("name") or "the student", summary=summary_text
        )

    kwargs = dict(
        response_modalities=["AUDIO"],
        system_instruction=system_instruction,
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=conv_config.get("voice_name") or DEFAULT_VOICE
                )
            )
        ),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        ),
        session_resumption=types.SessionResumptionConfig(handle=resumption_handle),
    )

    model_info = next((m for m in MODEL_OPTIONS if m["id"] == model_name), None)
    if model_info and model_info.get("supports_affective_dialog"):
        try:
            return types.LiveConnectConfig(**kwargs, enable_affective_dialog=True)
        except TypeError:
            # Preview-API field name mismatch on this google-genai version -
            # degrade gracefully rather than break the whole session.
            print("[build_config] enable_affective_dialog not supported by installed SDK - continuing without it.")

    return types.LiveConnectConfig(**kwargs)


# --- Page routes ---

@app.get("/")
async def serve_learning_page(request: Request):
    # Newer Starlette moved `request` to the first positional argument of
    # TemplateResponse (the old `(name, {"request": request})` calling
    # convention silently shifts the context dict into the `name` slot
    # instead, which blows up inside Jinja2's template cache with
    # "unhashable type: 'dict'" - request must be passed explicitly here).
    return templates.TemplateResponse(request, "pages/learning.html")


@app.get("/landing")
async def serve_landing_page(request: Request):
    return templates.TemplateResponse(request, "pages/landing.html")


@app.get("/avatar-select")
async def serve_avatar_select_page(request: Request):
    return templates.TemplateResponse(request, "pages/avatar_select.html")


@app.get("/profiles")
async def serve_profiles_page(request: Request):
    return templates.TemplateResponse(request, "pages/profiles.html")


# --- Reference data endpoints ---

@app.get("/api/voices")
def get_voices():
    return {"voices": VOICE_OPTIONS, "default": DEFAULT_VOICE}


@app.get("/api/models")
def get_models():
    return {"models": MODEL_OPTIONS, "default": DEFAULT_MODEL}


@app.get("/api/avatars")
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


@app.get("/api/profiles")
def list_profiles():
    return {"profiles": [{"id": p["id"], "name": p["name"]} for p in load_profiles()]}


@app.post("/api/profiles")
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


@app.get("/api/profiles/{profile_id}")
def get_profile(profile_id: str):
    profile = get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    return profile


@app.put("/api/profiles/{profile_id}")
async def update_profile(profile_id: str, request: Request):
    payload = await request.json()
    fields = {k: v for k, v in payload.items() if k in PROFILE_EDITABLE_FIELDS}
    updated = patch_profile(profile_id, fields)
    if updated is None:
        raise HTTPException(404, "Profile not found")
    return updated


@app.delete("/api/profiles/{profile_id}")
def remove_profile(profile_id: str):
    if not delete_profile(profile_id):
        raise HTTPException(404, "Profile not found")
    for conv in memory.list_conversations(profile_id):
        memory.delete_conversation(conv["id"])
    return {"deleted": True}


# --- Conversations REST API (per profile) ---

@app.get("/api/profiles/{profile_id}/conversations")
def list_conversations_endpoint(profile_id: str):
    profile = get_profile_by_id(profile_id)
    if profile is None:
        raise HTTPException(404, "Profile not found")
    return {
        "conversations": memory.list_conversations(profile_id),
        "active_conversation_id": profile.get("active_conversation_id"),
    }


@app.post("/api/profiles/{profile_id}/conversations")
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
    }
    name = (payload.get("name") or "").strip() or None
    conv = memory.create_conversation(profile_id, config, name=name)
    patch_profile(profile_id, {"active_conversation_id": conv["id"]})
    return conv


@app.get("/api/profiles/{profile_id}/conversations/{conversation_id}")
def get_conversation_endpoint(profile_id: str, conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")
    turns = memory.get_turns(conversation_id)
    summary = memory.get_summary(conversation_id)
    return {**conv, "turns": turns, "summary": summary["summary"] if summary else None}


@app.put("/api/profiles/{profile_id}/conversations/{conversation_id}")
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


@app.delete("/api/profiles/{profile_id}/conversations/{conversation_id}")
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


@app.get("/api/profiles/{profile_id}/conversations/{conversation_id}/notes")
def get_conversation_notes_endpoint(profile_id: str, conversation_id: str):
    conv = memory.get_conversation(conversation_id)
    if conv is None or conv["profile_id"] != profile_id:
        raise HTTPException(404, "Conversation not found")
    return {
        "vocab_mistakes": memory.get_vocab_mistakes(conversation_id),
        "lesson_log": memory.get_lesson_log(conversation_id),
    }


@app.put("/api/profiles/{profile_id}/active-conversation")
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


# --- Live session WebSocket ---

def is_dead_resumption_handle_error(e: Exception) -> bool:
    """A stored session_resumption handle can outlive its validity on
    Google's side (expiry, server-side eviction, etc.). When that happens
    the Live API rejects the connection outright with a 1008 close code -
    retrying with the same handle will just fail the same way every time,
    so this needs to be detected separately from ordinary transient errors.

    Originally this only matched the documented 'BidiGenerateContent
    session not found' wording. In practice a second, less specific 1008
    message ('The operation was aborted.') was also observed to be
    resumption-handle related - it repro'd specifically on reconnects that
    carried a stored handle, and stopped once the handle was dropped
    (switching models cleared it via the config-identity check below).
    Retrying that message unchanged just failed identically every attempt,
    so any 1008 is now treated as a signal to drop the handle and retry
    fresh rather than only the one documented phrasing.
    """
    return "1008" in str(e)


async def _connect_live_with_retries(client: genai.Client, model_name: str, config: types.LiveConnectConfig):
    """Opens a Live API session, retrying transient connect failures.

    Returns (live_cm, live_session, handle_was_dropped): the caller is
    responsible for calling live_cm.__aexit__ when done (can't use a normal
    `async with` here since the retry loop needs to wrap just the connection
    attempt, not the whole conversation), and handle_was_dropped tells the
    caller whether a resumption handle that looked valid going in was
    rejected as dead by Google and silently swapped out for a fresh session,
    so it can clear that dead handle from storage and report status honestly.
    """
    last_exc: Exception | None = None
    handle_was_dropped = False
    for attempt in range(RETRY_ATTEMPTS + 1):
        live_cm = client.aio.live.connect(model=model_name, config=config)
        try:
            live_session = await live_cm.__aenter__()
            return live_cm, live_session, handle_was_dropped
        except Exception as e:
            last_exc = e
            if is_dead_resumption_handle_error(e) and config.session_resumption and config.session_resumption.handle:
                print("[ws_session] session_resumption handle rejected (expired/unknown) - retrying fresh, without it.")
                config.session_resumption.handle = None
                handle_was_dropped = True
                continue
            if not retry.is_transient_error(e) or attempt == RETRY_ATTEMPTS:
                raise
            delay = retry.parse_retry_delay(str(e)) or (RETRY_BASE_DELAY * (2 ** attempt))
            print(f"[ws_session] connect attempt {attempt + 1} failed ({type(e).__name__}) - retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, keeps type checkers happy


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    await websocket.accept()

    try:
        init_msg = await websocket.receive_json()
    except WebSocketDisconnect:
        return

    if init_msg.get("type") != "init":
        await websocket.send_json({"type": "error", "message": "First message must be type 'init'."})
        await websocket.close(code=1002)
        return

    profile_id = init_msg.get("profile_id")
    profile = get_profile_by_id(profile_id) if profile_id else None
    conv = None

    if profile is not None:
        requested_cid = init_msg.get("conversation_id")
        if requested_cid:
            conv = memory.get_conversation(requested_cid)
            if conv is not None and conv["profile_id"] != profile_id:
                conv = None
        if conv is None:
            conv = get_active_conversation(profile)
        if conv is None:
            await websocket.send_json({
                "type": "error",
                "message": "This profile doesn't have a language set up yet - add one first.",
            })
            await websocket.close()
            return

        # Inline overrides (e.g. a no-profile-yet fallback caller, or a
        # client that still sends these) only apply if the conversation's
        # own stored config doesn't already have them - the conversation is
        # the source of truth for voice/language/model once it exists.
        conv_config = dict(conv["config"])
        patch_profile(profile_id, {"active_conversation_id": conv["id"]})
    else:
        # No profile selected yet (first-run fallback) - fully ephemeral,
        # no persisted memory, config comes straight from the init message.
        profile = {"id": None, "name": init_msg.get("profile_name") or "the student", "api_key": init_msg.get("api_key")}
        conv_config = {
            "voice_name": init_msg.get("voice_name") or DEFAULT_VOICE,
            "native_language": init_msg.get("native_language") or DEFAULT_NATIVE_LANGUAGE,
            "target_language": init_msg.get("target_language") or DEFAULT_TARGET_LANGUAGE,
            "model_name": init_msg.get("model_name") or DEFAULT_MODEL,
        }

    model_name = conv_config.get("model_name") or DEFAULT_MODEL
    print(f"[ws_session] init: profile={profile.get('name')!r} conversation={(conv or {}).get('name')!r} model={model_name!r}")

    # The config (voice/language/model) this conversation actually wants. If
    # it differs from the config that produced the currently stored
    # resumption handle, the handle would just resume the OLD session with
    # its baked-in settings - so drop it and open a fresh session, re-seeded
    # from the conversation's rolling summary instead. Otherwise (plain
    # reconnect: network blip, session time limit) we resume and keep the
    # conversation context Google is already holding.
    config_identity = dict(conv_config)
    resumption_handle = None
    summary_text = None

    if conv is not None:
        if conv.get("resumption_config") != config_identity:
            if conv.get("resumption_handle") is not None:
                print("[ws_session] config changed since last session on this conversation - starting fresh instead of resuming.")
            memory.clear_resumption(conv["id"])
            conv = memory.get_conversation(conv["id"])
        resumption_handle = conv.get("resumption_handle")

        # Fetched unconditionally (not just when resumption_handle is None):
        # a handle that looks valid here can still be rejected as dead by
        # Google at connect time (see is_dead_resumption_handle_error below),
        # and by then it's too late to go back and fetch this. Injecting it
        # even on a genuine resume is harmless - short prompt, and it's
        # redundant with context Google is already holding in that case.
        summary_row = memory.get_summary(conv["id"])
        if summary_row:
            summary_text = summary_row["summary"]

    config = build_config(profile, conv_config, model_name, resumption_handle=resumption_handle, summary_text=summary_text)

    try:
        client = get_client_for_key(profile.get("api_key"))
    except ValueError as e:
        print(f"[ws_session] {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"{e} Add one for this profile on the landing page (or /profiles) and try again.",
            })
        except Exception:
            pass
        await websocket.close()
        return

    try:
        live_cm, live_session, handle_was_dropped = await _connect_live_with_retries(client, model_name, config)
        if handle_was_dropped and conv is not None:
            print(f"[ws_session] session_resumption handle for conversation={conv['id']!r} was rejected as dead - cleared.")
            memory.clear_resumption(conv["id"])
    except Exception as e:
        print(f"[ws_session] connect failed after retries: {type(e).__name__}: {e}")
        try:
            await websocket.send_json({
                "type": "error",
                "message": f"Could not connect to the tutor after retrying: {type(e).__name__}: {e}",
            })
        except Exception:
            pass
        await websocket.close()
        return

    resumed = resumption_handle is not None and not handle_was_dropped
    try:
        await websocket.send_json({
            "type": "session_status",
            "resumed": resumed,
            "conversation_name": (conv or {}).get("name"),
        })
    except Exception:
        pass

    # Streamed transcript chunks get batched into one row per speaker per
    # turn (not one row per chunk) and flushed to SQLite when Gemini signals
    # turn_complete. Both sides come from Gemini's own Live API transcription
    # (input_audio_transcription / output_audio_transcription - see
    # build_config) and stream in the same way, so both are buffered here
    # identically.
    turn_buffer = {"user": [], "tutor": []}

    async def flush_turn_buffer_to_memory():
        if conv is None:
            turn_buffer["user"].clear()
            turn_buffer["tutor"].clear()
            return

        user_text = "".join(turn_buffer["user"])
        tutor_text = "".join(turn_buffer["tutor"])
        turn_buffer["user"].clear()
        turn_buffer["tutor"].clear()
        if user_text.strip():
            memory.insert_turn(conv["id"], "user", user_text)
        if tutor_text.strip():
            memory.insert_turn(conv["id"], "tutor", tutor_text)

        prev = memory.get_summary(conv["id"])
        since = prev["based_on_turn"] if prev else 0
        if memory.get_turn_count(conv["id"]) - since >= memory.SUMMARY_FOLD_EVERY_N_TURNS:
            asyncio.create_task(asyncio.to_thread(summarize_conversation, conv["id"], profile.get("name") or "the student", profile.get("api_key")))

    try:
        async def browser_to_live():
            while True:
                msg = await websocket.receive_json()
                msg_type = msg.get("type")

                try:
                    if msg_type == "start_turn":
                        await live_session.send_realtime_input(activity_start=types.ActivityStart())
                    elif msg_type == "audio_chunk":
                        pcm_bytes = base64.b64decode(msg["data"])
                        await live_session.send_realtime_input(
                            audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
                        )
                    elif msg_type == "turn_complete":
                        await live_session.send_realtime_input(activity_end=types.ActivityEnd())
                    elif msg_type == "close":
                        return
                except Exception as e:
                    print(f"[browser_to_live] '{msg_type}' failed: {type(e).__name__}: {e}")
                    await websocket.send_json({
                        "type": "error",
                        "message": f"'{msg_type}' failed: {type(e).__name__}: {e}",
                    })
                    raise

        async def live_to_browser():
            while True:
                async for response in live_session.receive():
                    sc = getattr(response, "server_content", None)

                    if getattr(response, "data", None):
                        await websocket.send_json({
                            "type": "audio",
                            "data": base64.b64encode(response.data).decode("ascii"),
                        })

                    in_transcript = getattr(sc, "input_transcription", None) if sc else None
                    in_text = getattr(in_transcript, "text", None) if in_transcript else None
                    if in_text:
                        turn_buffer["user"].append(in_text)
                        await websocket.send_json({"type": "transcript_in", "text": in_text})

                    out_transcript = getattr(sc, "output_transcription", None) if sc else None
                    out_text = getattr(out_transcript, "text", None) if out_transcript else None
                    if out_text:
                        turn_buffer["tutor"].append(out_text)
                        await websocket.send_json({"type": "transcript_out", "text": out_text})

                    resumption_update = getattr(response, "session_resumption_update", None)
                    if resumption_update and getattr(resumption_update, "resumable", False):
                        new_handle = getattr(resumption_update, "new_handle", None)
                        if conv is not None and new_handle:
                            memory.set_resumption(conv["id"], new_handle, config_identity)

                    if sc and getattr(sc, "turn_complete", False):
                        await flush_turn_buffer_to_memory()
                        await websocket.send_json({"type": "turn_complete"})

        tasks = [asyncio.create_task(browser_to_live()), asyncio.create_task(live_to_browser())]
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                print(f"[ws_session] task raised: {type(exc).__name__}: {exc}")

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[ws_session] outer exception: {type(e).__name__}: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        await flush_turn_buffer_to_memory()
        if conv is not None:
            # Final fold on disconnect - catches the tail so a session that
            # ends mid-way through a summarization interval isn't lost, and
            # is cheap/best-effort like every other summarization call.
            try:
                await asyncio.to_thread(summarize_conversation, conv["id"], profile.get("name") or "the student", profile.get("api_key"))
            except Exception:
                pass
        try:
            await live_cm.__aexit__(None, None, None)
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


# html=True is unneeded now - "/" is served by the explicit route above,
# rendered via Jinja2 rather than a flat static index.html. This mount just
# serves everything else under static/ as plain files (UI/styles, UI/
# scripts, vendor/, avatar/, voices/, pcm-processor.js, LanguLearn.ico).
app.mount("/", StaticFiles(directory="static"), name="static")
