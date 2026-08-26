"""Gemini Live API relay: config building, connect-retry handling, and the
/ws/session websocket route. Split out of main.py.

Turn signaling (push-to-talk):
    client sends "init"          -> session config, must be the first WS message
    client sends "start_turn"    -> server sends activity_start to Gemini
    client streams "audio_chunk" -> forwarded as realtime audio input
    client sends "turn_complete" -> server sends activity_end to Gemini

Turn signaling (hands-free mode):
    client sends "handsfree_start"        -> mic is live; server resets its window buffer
    client streams "handsfree_chunk"      -> raw PCM, continuous (no explicit turn boundary from the client)
    client sends "handsfree_stop"         -> mic muted; server closes out any open turn

    The server itself decides turn boundaries here, since the client has no
    natural start/stop signal when the mic never closes. Incoming audio is
    accumulated into rolling ~2s windows (HANDSFREE_WINDOW_BYTES); each
    window is speaker-verified (speech_detection.verify) against the
    profile's enrolled voice before being forwarded to Gemini - this can't
    piggyback on Gemini's own turn-complete signal, since the decision to
    forward has to happen before Gemini ever sees the audio. A window with
    real energy that fails verification (someone else talking) is dropped
    silently without ending an in-progress turn; a window at/below
    HANDSFREE_SILENCE_RMS_THRESHOLD is treated as a pause and closes the
    turn (activity_end) if one was open. See handle_handsfree_window below.

Both sides of the transcript come directly from Gemini's own Live API:
input_audio_transcription for the student's side, output_audio_transcription
for the tutor's - see build_config below.

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

Mood: the tutor's avatar expression is driven two ways - a mood_change
message forwarded here whenever Gemini calls the set_mood tool (silent,
model's discretion, driven by tutor_instructions.CONVERSATIONAL_RULES rule
5), and a client-side-only idle-timeout 'sleep' state the frontend manages
entirely on its own (armIdleSleepTimer in audio.js) - this server never
sends or knows about 'sleep'.

Retry behavior: Gemini's preview models occasionally return a transient
"500 INTERNAL", "503 UNAVAILABLE", or (less often) a 429 rate-limit error
that succeeds if you just try again - see retry.py for the shared
classification/backoff logic. The initial Live session connection retries
up to RETRY_ATTEMPTS additional times with exponential backoff (or Google's
own suggested retryDelay when present) before giving up. Errors that aren't
recognizably transient (bad request, auth, unknown model, etc.) fail
immediately instead of being retried pointlessly.

go_away/1011 reconnects: two different signals both mean "this Gemini
session is ending, open a new one" - go_away is Google's own advance
notice (session nearing its time/context limit), 1011 is an abrupt close.
Both are handled the same way, in the same block in ws_session: tear down
the old live_cm, connect a fresh one (resumed via the conversation's
stored resumption_handle when possible), replay any audio chunks that were
mid-turn when the old session ended, and send a new session_status - all
without ever closing the BROWSER's own websocket, so the person never has
to notice or reconnect manually. go_away additionally waits for the
current turn to finish (via go_away_pending in live_to_browser) before
reconnecting, since Google gives a time_left warning rather than closing
immediately; 1011 has no such warning and reconnects right away. Only 1011
ever switches to the fallback model - go_away reconnects to the SAME
model, since nothing about it indicates that model is unhealthy.
"""

import asyncio
import base64
import re
import time
from datetime import date

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from . import memory, observability, quizzes, retry, scenarios, speech_detection
from .constants import (
    DEFAULT_DIFFICULTY,
    DEFAULT_MIC_CALIBRATION_KEY,
    DEFAULT_MODEL,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_VOICE,
    HANDSFREE_SILENCE_RMS_THRESHOLD,
    HANDSFREE_WINDOW_BYTES,
    MODEL_OPTIONS,
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY,
    SPEAKER_VERIFICATION_THRESHOLD,
    VOICE_OPTIONS,
    get_api_voice_name,
)
from .profiles_store import get_client_for_key, get_profile_by_id, patch_profile
from .summarization import summarize_conversation
from .tutor_instructions import build_system_instruction
from .tutor_tools import MOOD_TOOL, QUIZ_TOOL

router = APIRouter()

# Message types that carry voice input - dropped without any response while
# a quiz is open (quiz_state["active"]) rather than forwarded to Gemini, per
# the permanent no-mid-quiz-interruption decision. turn_complete is
# included alongside start_turn/audio_chunk/handsfree_* even though it
# carries no audio itself, since honoring one without the other could send
# an activity_end with no matching activity_start.
_VOICE_MESSAGE_TYPES = frozenset(
    {"start_turn", "audio_chunk", "turn_complete", "handsfree_start", "handsfree_chunk", "handsfree_stop"}
)


def _quiz_results_summary(items: list[dict]) -> str:
    """Builds the bracketed, non-spoken text turn injected into the live
    session once a quiz is done (see quiz_done handling in ws_session) -
    tutor_instructions.GUARDRAILS tells the tutor to react to a message
    shaped like this conversationally rather than read it aloud."""
    total = len(items)
    correct = sum(1 for i in items if i["is_correct"])
    summary = f"[Quiz results: {correct}/{total} correct."
    missed = [i for i in items if not i["is_correct"]]
    if missed:
        missed_desc = ", ".join(f"'{i['target_term']}' (wrote '{i['student_answer'] or ''}')" for i in missed)
        summary += f" Missed: {missed_desc}."
    return summary + "]"


_BLANK_RE = re.compile(r"\{\d+\}")


def _validate_quiz_items(items: list[dict]) -> None:
    """Defense-in-depth only - QUIZ_TOOL's schema already makes
    correct_answers required for every item (see tutor_tools.py's
    normalized item shape), so this should rarely fire in practice. a fill_blank_dragdrop item whose
    correct_answers length doesn't match its blank count, so a schema-
    level failure is still visible in the console/Langfuse instead of
    silently reaching the student as a broken, unwinnable slide.
    """
    for idx, item in enumerate(items):
        if not isinstance(item, dict) or item.get("item_type") != "fill_blank_dragdrop":
            continue
        blank_count = len(_BLANK_RE.findall(item.get("text_with_blanks") or ""))
        answer_count = len(item.get("correct_answers") or [])
        if blank_count != answer_count:
            print(
                f"[start_quiz] item {idx} blank/answer count mismatch: "
                f"{blank_count} blanks in text_with_blanks vs {answer_count} correct_answers - {item!r}"
            )


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


def build_config(
    profile: dict,
    conv_config: dict,
    model_name: str,
    resumption_handle: str | None = None,
    summary_text: str | None = None,
    review_terms: list[str] | None = None,
) -> types.LiveConnectConfig:
    name = profile.get("name") or "the student"
    native_language = conv_config.get("native_language") or DEFAULT_NATIVE_LANGUAGE
    target_language = conv_config.get("target_language") or DEFAULT_TARGET_LANGUAGE
    voice_name = conv_config.get("voice_name") or DEFAULT_VOICE
    tutor_name = next(
        (v.get("alias") or v["name"] for v in VOICE_OPTIONS if v["name"] == voice_name),
        voice_name,
    )

    scenario_id = conv_config.get("scenario") or scenarios.DEFAULT_SCENARIO
    scenario_template = scenarios.SCENARIO_TEMPLATES.get(scenario_id, scenarios.SCENARIO_TEMPLATES[scenarios.DEFAULT_SCENARIO])
    difficulty = conv_config.get("difficulty") or DEFAULT_DIFFICULTY

    system_instruction = build_system_instruction(
        scenario_template,
        name=name,
        native_language=native_language,
        target_language=target_language,
        tutor_name=tutor_name,
        difficulty=difficulty,
        summary_text=summary_text,
        review_terms=review_terms,
    )

    kwargs = {
        "response_modalities": ["AUDIO"],
        "system_instruction": system_instruction,
        "tools": [MOOD_TOOL, QUIZ_TOOL],
        "input_audio_transcription": types.AudioTranscriptionConfig(),
        "output_audio_transcription": types.AudioTranscriptionConfig(),
        "speech_config": types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name=get_api_voice_name(conv_config.get("voice_name") or DEFAULT_VOICE)
                )
            )
        ),
        "realtime_input_config": types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        ),
        "session_resumption": types.SessionResumptionConfig(handle=resumption_handle),
    }

    # Extends how long a Live session can run before Google forcibly ends it
    # (a go_away near the default time/context limit) by compressing older
    # context instead of just truncating - added defensively (not baked into
    # kwargs above) since it's a newer field that may not exist on every
    # installed SDK version, same reasoning as enable_affective_dialog below.
    # Worth having regardless of whether it's the actual cause of any given
    # "the tutor tried to end the session" report, since a session that's
    # further from its length limit gives the model less reason to wrap up.
    try:
        kwargs["context_window_compression"] = types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow())
    except (AttributeError, TypeError):
        print("[build_config] context_window_compression not supported by installed SDK - continuing without it.")

    model_info = next((m for m in MODEL_OPTIONS if m["id"] == model_name), None)
    if model_info and model_info.get("supports_affective_dialog"):
        try:
            return types.LiveConnectConfig(**kwargs, enable_affective_dialog=True)
        except TypeError:
            # Preview-API field name mismatch on this google-genai version -
            # degrade gracefully rather than break the whole session. Falls
            # through to the hardened plain return below, which has its own
            # safety net for context_window_compression.
            print("[build_config] enable_affective_dialog not supported by installed SDK - continuing without it.")

    try:
        return types.LiveConnectConfig(**kwargs)
    except TypeError:
        # Belt-and-suspenders for context_window_compression: the try/except
        # above only catches the classes themselves not existing - if they DO
        # exist but LiveConnectConfig itself doesn't accept the field yet (a
        # partial-support SDK version), this is where that would surface.
        # Drop it and retry once rather than crash session creation entirely.
        kwargs.pop("context_window_compression", None)
        print(
            "[build_config] context_window_compression rejected by LiveConnectConfig on this SDK version - continuing without it."
        )
        return types.LiveConnectConfig(**kwargs)


def is_dead_resumption_handle_error(e: Exception) -> bool:
    """A stored session_resumption handle can outlive its validity on
    Google's side (expiry, server-side eviction, etc.). When that happens
    the Live API rejects the connection outright with a 1008 close code -
    retrying with the same handle will just fail the same way every time,
    so this needs to be detected separately from ordinary transient errors.

    Matches any 1008 close, not just the documented 'BidiGenerateContent
    session not found' wording - a less specific '1008: The operation was
    aborted.' message can also indicate a dead handle, and retrying that
    unchanged fails identically every attempt either way.
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
            delay = retry.parse_retry_delay(str(e)) or (RETRY_BASE_DELAY * (2**attempt))
            print(f"[ws_session] connect attempt {attempt + 1} failed ({type(e).__name__}) - retrying in {delay:.1f}s...")
            await asyncio.sleep(delay)
    raise last_exc  # unreachable, keeps type checkers happy


def _connect_failure_payload(exc: Exception, fallback_message: str) -> dict:
    """Picks the message shown to the user for a connect failure that's
    exhausted every retry/fallback - a friendly, specific message for the
    two classifiable cases (no internet; genuinely out of free-tier quota),
    the raw exception text otherwise. Shared by both call sites below so
    the classification logic (retry.is_network_error /
    retry.is_rate_limit_error) only lives in one place.
    """
    if retry.is_network_error(exc):
        return {
            "type": "error",
            "kind": "network",
            "message": "Couldn't reach the tutor - check your internet connection and try again.",
        }
    if retry.is_rate_limit_error(exc):
        return {
            "type": "error",
            "kind": "rate_limit",
            "message": "You've hit the free-tier quota limit - try again in a bit.",
        }
    return {"type": "error", "message": fallback_message}


def _record_active_day(profile: dict) -> None:
    """Updates last_active_date/current_streak (Settings modal Stats tab -
    see stats.py) once per calendar day this profile opens a Live session.
    Uses the server's own local date, not UTC - this is a self-hosted app
    running on the student's own machine, so server-local time already IS
    the student's local time, and "today"/streaks should mean that, not a
    UTC day boundary that could flip mid-evening for them.

    A gap of exactly one day extends the streak; any other gap (including
    0, already handled above) resets it to 1. No no-profile-yet fallback
    session (profile["id"] is None) ever reaches here - see its only call
    site in ws_session.
    """
    today = date.today().isoformat()
    last = profile.get("last_active_date")
    if last == today:
        return  # already recorded today - a same-day reconnect shouldn't double-increment
    streak = 1
    if last:
        try:
            gap = (date.fromisoformat(today) - date.fromisoformat(last)).days
            if gap == 1:
                streak = (profile.get("current_streak") or 0) + 1
        except ValueError:
            pass  # malformed stored date (shouldn't happen) - treat as a fresh start rather than crash the session
    patch_profile(profile["id"], {"last_active_date": today, "current_streak": streak})
    profile["last_active_date"] = today
    profile["current_streak"] = streak


@router.websocket("/ws/session")
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
            await websocket.send_json(
                {
                    "type": "error",
                    "message": "This profile doesn't have a language set up yet - add one first.",
                }
            )
            await websocket.close()
            return

        # Inline overrides (e.g. a no-profile-yet fallback caller, or a
        # client that still sends these) only apply if the conversation's
        # own stored config doesn't already have them - the conversation is
        # the source of truth for voice/language/model once it exists.
        conv_config = dict(conv["config"])
        patch_profile(profile_id, {"active_conversation_id": conv["id"]})
        _record_active_day(profile)
    else:
        # No profile selected yet (first-run fallback) - fully ephemeral,
        # no persisted memory, config comes straight from the init message.
        profile = {
            "id": None,
            "name": init_msg.get("profile_name") or "the student",
            "api_key": init_msg.get("api_key"),
        }
        conv_config = {
            "voice_name": init_msg.get("voice_name") or DEFAULT_VOICE,
            "native_language": init_msg.get("native_language") or DEFAULT_NATIVE_LANGUAGE,
            "target_language": init_msg.get("target_language") or DEFAULT_TARGET_LANGUAGE,
            "model_name": init_msg.get("model_name") or DEFAULT_MODEL,
            "scenario": init_msg.get("scenario") or scenarios.DEFAULT_SCENARIO,
            "difficulty": init_msg.get("difficulty") or DEFAULT_DIFFICULTY,
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
    review_terms = None

    if conv is not None:
        if conv.get("resumption_config") != config_identity:
            if conv.get("resumption_handle") is not None:
                print("[ws_session] config changed since last session on this conversation - starting fresh instead of resuming.")
            memory.clear_resumption(conv["id"])
            conv = memory.get_conversation(conv["id"])
        resumption_handle = conv.get("resumption_handle")
        print(
            f"[ws_session] resumption lookup for conversation={conv['id']!r}: stored_handle={'<none>' if resumption_handle is None else resumption_handle[:12] + '...'}"
        )

        # Fetched unconditionally (not just when resumption_handle is None):
        # a handle that looks valid here can still be rejected as dead by
        # Google at connect time (see is_dead_resumption_handle_error below),
        # and by then it's too late to go back and fetch this. Injecting it
        # even on a genuine resume is harmless - short prompt, and it's
        # redundant with context Google is already holding in that case.
        summary_row = memory.get_summary(conv["id"])
        if summary_row:
            summary_text = summary_row["summary"]

        # Same reasoning as summary_text above - fetched unconditionally,
        # not just on a fresh session, and computed once here rather than
        # per build_config call so every fallback/reconnect path below sees
        # the same list a single connect settled on.
        review_terms = memory.get_review_candidates(conv["id"])

    config = build_config(
        profile,
        conv_config,
        model_name,
        resumption_handle=resumption_handle,
        summary_text=summary_text,
        review_terms=review_terms,
    )

    observability.set_profile_keys(
        profile.get("langfuse_public_key"),
        profile.get("langfuse_secret_key"),
        profile.get("langfuse_base_url"),
    )

    try:
        client = get_client_for_key(profile.get("api_key"))
    except ValueError as e:
        print(f"[ws_session] {e}")
        try:
            await websocket.send_json(
                {
                    "type": "error",
                    "message": f"{e} Add one for this profile on the landing page (or /profiles) and try again.",
                }
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[ws_session] send_json failed: {exc}")
        await websocket.close()
        return

    # Computed once, up front, so it covers both failure paths that need a
    # fallback: the initial connect attempt below, and the mid-session 1011
    # handling further down.
    fallback_model = None
    if model_name:
        for candidate in MODEL_OPTIONS:
            if candidate["id"] != model_name:
                fallback_model = candidate["id"]
                break

    try:
        live_cm, live_session, handle_was_dropped = await _connect_live_with_retries(client, model_name, config)
        if handle_was_dropped and conv is not None:
            print(f"[ws_session] session_resumption handle for conversation={conv['id']!r} was rejected as dead - cleared.")
            memory.clear_resumption(conv["id"])
    except Exception as first_exc:  # noqa: BLE001
        if fallback_model:
            print(
                f"[ws_session] initial connect to {model_name!r} failed ({type(first_exc).__name__}) - trying fallback model {fallback_model!r} before giving up."
            )
            prev_model, model_name = model_name, fallback_model
            fallback_model = prev_model
            if conv is not None:
                conv_config["model_name"] = model_name
                config_identity = dict(conv_config)
                memory.update_conversation(conv["id"], config=conv_config)
            config = build_config(
                profile,
                conv_config,
                model_name,
                resumption_handle=resumption_handle,
                summary_text=summary_text,
                review_terms=review_terms,
            )
            try:
                (
                    live_cm,
                    live_session,
                    handle_was_dropped,
                ) = await _connect_live_with_retries(client, model_name, config)
                if handle_was_dropped and conv is not None:
                    memory.clear_resumption(conv["id"])
                    resumption_handle = None
            except Exception as second_exc:  # noqa: BLE001
                print(
                    f"[ws_session] fallback connect to {model_name!r} also failed ({type(second_exc).__name__}) - no models available."
                )
                try:
                    await websocket.send_json(
                        _connect_failure_payload(
                            second_exc,
                            f"Could not connect to either configured model: {type(second_exc).__name__}: {second_exc}",
                        )
                    )
                    await websocket.send_json(
                        {
                            "type": "session_status",
                            "model_name": None,
                            "unavailable": True,
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"[ws_session] send_json failed: {exc}")
                await websocket.close()
                return
        else:
            print(f"[ws_session] connect failed after retries: {type(first_exc).__name__}: {first_exc}")
            try:
                await websocket.send_json(
                    _connect_failure_payload(
                        first_exc,
                        f"Could not connect to the tutor after retrying: {type(first_exc).__name__}: {first_exc}",
                    )
                )
                await websocket.send_json({"type": "session_status", "model_name": None, "unavailable": True})
            except Exception as exc:  # noqa: BLE001
                print(f"[ws_session] send_json failed: {exc}")
            await websocket.close()
            return

    resumed = resumption_handle is not None and not handle_was_dropped
    try:
        await websocket.send_json(
            {
                "type": "session_status",
                "resumed": resumed,
                "conversation_name": (conv or {}).get("name"),
                "model_name": model_name,
            }
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[ws_session] send_json failed: {exc}")

    # Stats tab's "hours studied" figure (stats.py) - one continuous
    # wall-clock span per Live session, from here (connected and about to
    # start relaying) to the accumulation in this function's finally block
    # below, regardless of how many go_away/1011 reconnects happen to the
    # underlying Gemini session in between (those replace live_session
    # in-place without this browser websocket ever closing - see the
    # module docstring). time.monotonic() rather than wall-clock time
    # since only the elapsed duration matters, never an absolute timestamp,
    # and monotonic is immune to the system clock changing mid-session.
    session_start_monotonic = time.monotonic()

    # Voice input is gated (see _VOICE_MESSAGE_TYPES) while quiz_state["active"]
    # is True - set either here (an unfinished quiz from an earlier app
    # session, resumed below) or later when the tutor calls start_quiz mid-
    # conversation (see the tool_call handling in live_to_browser).
    quiz_state = {"active": False, "quiz_id": None}
    if conv is not None:
        in_progress_quiz = quizzes.get_in_progress_quiz(conv["id"])
        if in_progress_quiz is not None:
            quiz_state["active"] = True
            quiz_state["quiz_id"] = in_progress_quiz["quiz_id"]
            try:
                await websocket.send_json(
                    {
                        "type": "quiz_resume",
                        "quiz_id": in_progress_quiz["quiz_id"],
                        "quiz_type": in_progress_quiz["quiz_type"],
                        "items": in_progress_quiz["payload"].get("items"),
                        "current_index": in_progress_quiz["current_index"],
                        "answered_items": in_progress_quiz["answered_items"],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[ws_session] send_json failed: {exc}")

    # Streamed transcript chunks get batched into one row per speaker per
    # turn (not one row per chunk) and flushed to SQLite when Gemini signals
    # turn_complete. Both sides come from Gemini's own Live API transcription
    # (input_audio_transcription / output_audio_transcription - see
    # build_config) and stream in the same way, so both are buffered here
    # identically.
    turn_buffer = {"user": [], "tutor": []}

    # Hands-free mode's own turn-tracking state - see module docstring.
    # "buffer" accumulates raw PCM bytes toward the next ~2s window;
    # "turn_active" tracks whether an activity_start has been sent to the
    # CURRENT live_session without a matching activity_end yet, so it gets
    # reset to False at every point below where live_session is replaced
    # (a fresh Gemini session has no memory of the old one's open turn).
    hf_state = {"active": False, "buffer": bytearray(), "turn_active": False}
    # Which mic entry to read calibration from - keyed by mic label, same
    # key handsfreeSetup.js writes to (see constants.DEFAULT_MIC_CALIBRATION_KEY
    # for the default-mic sentinel). Falls back to the global defaults if
    # this specific mic was never calibrated (shouldn't normally happen -
    # audio.js routes to /handsfree-setup first - but a stale/edited
    # profile shouldn't crash a session over it).
    mic_key = profile.get("mic_label") or DEFAULT_MIC_CALIBRATION_KEY
    mic_calibration = (profile.get("mic_calibrations") or {}).get(mic_key) or {}
    hf_silence_threshold = mic_calibration.get("silence_rms_threshold") or HANDSFREE_SILENCE_RMS_THRESHOLD
    hf_similarity_threshold = mic_calibration.get("speaker_threshold") or SPEAKER_VERIFICATION_THRESHOLD

    async def flush_turn_buffer_to_memory(current_turn_chunks=None, hf_turn_chunks=None):
        user_text = "".join(turn_buffer["user"])
        tutor_text = "".join(turn_buffer["tutor"])
        turn_buffer["user"].clear()
        turn_buffer["tutor"].clear()

        # audio_chunks_sent reuses state that's already tracked for a
        # completely different reason (replaying a buffered turn to a
        # fallback model on a 1011 - see current_turn_chunks/hf_turn_chunks
        # above), not new bookkeeping added just for this. It's a concrete,
        # queryable signal for one specific failure mode worth being able to
        # search for later: a tutor turn with real spoken content but zero
        # forwarded audio chunks means the tutor generated a response with
        # no real student input behind it that turn.
        audio_chunks_sent = len(current_turn_chunks or []) + len(hf_turn_chunks or [])
        with observability.span(
            "conversation_turn",
            as_type="generation",
            input=user_text or None,
            model=model_name,
            metadata={"audio_chunks_sent": audio_chunks_sent},
            session_id=(conv or {}).get("id"),
            user_id=profile.get("id"),
        ) as gen:
            observability.update(gen, output=tutor_text or None)

            if conv is None:
                return

            if user_text.strip():
                memory.insert_turn(conv["id"], "user", user_text)
            if tutor_text.strip():
                memory.insert_turn(conv["id"], "tutor", tutor_text)

            prev = memory.get_summary(conv["id"])
            since = prev["based_on_turn"] if prev else 0
            if memory.get_turn_count(conv["id"]) - since >= memory.SUMMARY_FOLD_EVERY_N_TURNS:
                asyncio.create_task(
                    asyncio.to_thread(
                        summarize_conversation,
                        conv["id"],
                        profile.get("name") or "the student",
                        profile.get("api_key"),
                    )
                )

    try:
        while True:
            current_turn_chunks = []
            hf_turn_chunks = []  # mirrors current_turn_chunks, but for hands-free's forwarded windows - see handle_handsfree_window
            try:

                async def handle_handsfree_window(
                    window_bytes: bytes,
                    _live_session=live_session,
                    _hf_turn_chunks=hf_turn_chunks,
                ):
                    """One ~2s window of continuous hands-free mic audio: decide
                    silence vs speech vs "not the enrolled speaker", and forward,
                    drop, or close out the current turn accordingly. See the
                    module docstring's hands-free section for the full picture.
                    """
                    audio_f32 = speech_detection.pcm16_bytes_to_float32(window_bytes)
                    rms = float(np.sqrt(np.mean(audio_f32.astype(np.float64) ** 2))) if len(audio_f32) else 0.0

                    if rms < hf_silence_threshold:
                        if hf_state["turn_active"]:
                            await _live_session.send_realtime_input(activity_end=types.ActivityEnd())
                            hf_state["turn_active"] = False
                            _hf_turn_chunks.clear()  # turn completed normally - nothing left to replay on a later reconnect
                        return

                    try:
                        # CPU/torch work - off the event loop, same reasoning as
                        # summarize_conversation's asyncio.to_thread usage.
                        result = await asyncio.to_thread(
                            speech_detection.verify,
                            profile.get("id"),
                            mic_key,
                            audio_f32,
                            hf_silence_threshold,
                            hf_similarity_threshold,
                        )
                    except Exception as e:  # noqa: BLE001
                        import traceback

                        print(f"[handsfree] verification failed, forwarding unfiltered this window: {type(e).__name__}: {e}")
                        traceback.print_exc()
                        result = None  # fail open - a model hiccup shouldn't silently break hands-free mode

                    if result is not None and not result[0]:
                        print(f"[handsfree] window rejected (score={result[1]:.3f})")
                        return  # someone else is talking (or a false reject) - drop, keep any open turn as-is

                    if result is not None:
                        print(f"[handsfree] window accepted (score={result[1]:.3f}, rms={rms:.4f}) - forwarding")

                    if not hf_state["turn_active"]:
                        await _live_session.send_realtime_input(activity_start=types.ActivityStart())
                        hf_state["turn_active"] = True
                    # Recorded BEFORE the send, not after - if a 1011 cancels this
                    # coroutine while it's suspended inside the send_realtime_input
                    # await below (very plausible, since a 1011 typically kills
                    # both directions of the connection near-simultaneously), the
                    # window still needs to already be in hf_turn_chunks so it's
                    # not silently lost from replay. Mirrors audio_chunk's ordering
                    # above, which never had this bug.
                    _hf_turn_chunks.append(window_bytes)
                    await _live_session.send_realtime_input(audio=types.Blob(data=window_bytes, mime_type="audio/pcm;rate=16000"))

                async def browser_to_live(
                    _live_session=live_session,
                    _current_turn_chunks=current_turn_chunks,
                    _hf_turn_chunks=hf_turn_chunks,
                ):
                    while True:
                        msg = await websocket.receive_json()
                        msg_type = msg.get("type")

                        if quiz_state["active"] and msg_type in _VOICE_MESSAGE_TYPES:
                            continue  # voice input gated while a quiz is open - see quiz_state

                        try:
                            if msg_type == "start_turn":
                                _current_turn_chunks.clear()
                                await _live_session.send_realtime_input(activity_start=types.ActivityStart())
                            elif msg_type == "audio_chunk":
                                pcm_bytes = base64.b64decode(msg["data"])
                                _current_turn_chunks.append(pcm_bytes)
                                await _live_session.send_realtime_input(
                                    audio=types.Blob(data=pcm_bytes, mime_type="audio/pcm;rate=16000")
                                )
                            elif msg_type == "turn_complete":
                                await _live_session.send_realtime_input(activity_end=types.ActivityEnd())
                            elif msg_type == "handsfree_start":
                                hf_state["active"] = True
                                hf_state["buffer"] = bytearray()
                            elif msg_type == "handsfree_chunk":
                                if not hf_state["active"]:
                                    continue  # stray chunk after a mute race - ignore
                                hf_state["buffer"].extend(base64.b64decode(msg["data"]))
                                while len(hf_state["buffer"]) >= HANDSFREE_WINDOW_BYTES:
                                    window_bytes = bytes(hf_state["buffer"][:HANDSFREE_WINDOW_BYTES])
                                    del hf_state["buffer"][:HANDSFREE_WINDOW_BYTES]
                                    await handle_handsfree_window(window_bytes)
                            elif msg_type == "handsfree_stop":
                                hf_state["active"] = False
                                hf_state["buffer"] = bytearray()
                                if hf_state["turn_active"]:
                                    await _live_session.send_realtime_input(activity_end=types.ActivityEnd())
                                    hf_state["turn_active"] = False
                                    _hf_turn_chunks.clear()  # turn completed normally on mute - nothing left to replay
                            elif msg_type == "quiz_answer":
                                quizzes.record_item_answer(
                                    msg["quiz_id"],
                                    item_index=msg["item_index"],
                                    target_term=msg["target_term"],
                                    prompt_or_text=msg["prompt_or_text"],
                                    correct_answer=msg["correct_answer"],
                                    student_answer=msg.get("student_answer"),
                                    is_correct=bool(msg["is_correct"]),
                                )
                            elif msg_type == "quiz_done":
                                quiz_id = msg["quiz_id"]
                                quizzes.finalize_quiz_session(quiz_id, status="completed")
                                quiz_state["active"] = False
                                quiz_state["quiz_id"] = None
                                items = quizzes.get_quiz_items(quiz_id)
                                summary = _quiz_results_summary(items)
                                with observability.span(
                                    "quiz_done",
                                    input={"quiz_id": quiz_id},
                                    metadata={
                                        "total": len(items),
                                        "correct": sum(1 for i in items if i["is_correct"]),
                                    },
                                    session_id=(conv or {}).get("id"),
                                    user_id=profile.get("id"),
                                ):
                                    pass
                                try:
                                    await _live_session.send_client_content(
                                        turns=types.Content(role="user", parts=[types.Part(text=summary)]),
                                        turn_complete=True,
                                    )
                                except Exception as e:  # noqa: BLE001
                                    print(f"[browser_to_live] quiz results injection failed: {type(e).__name__}: {e}")
                            elif msg_type == "quiz_skip":
                                quiz_id = msg["quiz_id"]
                                quizzes.finalize_quiz_session(quiz_id, status="skipped")
                                quiz_state["active"] = False
                                quiz_state["quiz_id"] = None
                                answered = [i for i in quizzes.get_quiz_items(quiz_id) if i["student_answer"] is not None]
                                with observability.span(
                                    "quiz_skip",
                                    input={"quiz_id": quiz_id},
                                    metadata={"answered_count": len(answered)},
                                    session_id=(conv or {}).get("id"),
                                    user_id=profile.get("id"),
                                ):
                                    pass
                                if answered:
                                    correct = sum(1 for i in answered if i["is_correct"])
                                    summary = f"[Quiz skipped partway through: {correct}/{len(answered)} answered correctly before stopping.]"
                                else:
                                    summary = "[Quiz skipped before answering anything.]"
                                try:
                                    await _live_session.send_client_content(
                                        turns=types.Content(role="user", parts=[types.Part(text=summary)]),
                                        turn_complete=True,
                                    )
                                except Exception as e:  # noqa: BLE001
                                    print(f"[browser_to_live] quiz skip injection failed: {type(e).__name__}: {e}")
                            elif msg_type == "close":
                                return
                        except Exception as e:
                            print(f"[browser_to_live] '{msg_type}' failed: {type(e).__name__}: {e}")
                            await websocket.send_json(
                                {
                                    "type": "error",
                                    "message": f"'{msg_type}' failed: {type(e).__name__}: {e}",
                                }
                            )
                            raise

                async def live_to_browser(
                    _live_session=live_session,
                    _config_identity=config_identity,
                    _current_turn_chunks=current_turn_chunks,
                    _hf_turn_chunks=hf_turn_chunks,
                ):
                    go_away_pending = False
                    while True:
                        async for response in _live_session.receive():
                            sc = getattr(response, "server_content", None)
                            go_away = getattr(response, "go_away", None)
                            if go_away is not None:
                                go_away_pending = True
                                print(f"[ws_session] go_away received: {go_away}")
                                continue

                            if go_away_pending and sc and getattr(sc, "turn_complete", False):
                                go_away_pending = False
                                await flush_turn_buffer_to_memory(_current_turn_chunks, _hf_turn_chunks)
                                await websocket.send_json({"type": "turn_complete"})
                                return

                            if getattr(response, "data", None):
                                await websocket.send_json(
                                    {
                                        "type": "audio",
                                        "data": base64.b64encode(response.data).decode("ascii"),
                                    }
                                )

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

                            tool_call = getattr(response, "tool_call", None)
                            if tool_call:
                                function_responses = []
                                for fc in tool_call.function_calls:
                                    if fc.name == "set_mood":
                                        mood = (fc.args or {}).get("mood", "neutral")
                                        with observability.span(
                                            "tool_call:set_mood",
                                            input=dict(fc.args or {}),
                                            session_id=(conv or {}).get("id"),
                                            user_id=profile.get("id"),
                                        ):
                                            pass
                                        await websocket.send_json({"type": "mood_change", "mood": mood})
                                    elif fc.name == "start_quiz" and conv is not None:
                                        # Duplicate-quiz guard: if the student never
                                        # finished a previous quiz (possibly from an
                                        # earlier app session - already re-shown via
                                        # quiz_resume above), re-show that one instead
                                        # of starting a second one, so there's never
                                        # more than one in-progress quiz per
                                        # conversation to keep track of.
                                        existing = quizzes.get_in_progress_quiz(conv["id"])
                                        if existing is not None:
                                            quiz_id = existing["quiz_id"]
                                            payload = existing["payload"]
                                        else:
                                            payload = dict(fc.args or {})
                                            items = payload.get("items") or []
                                            _validate_quiz_items(items)
                                            quiz_id = quizzes.start_quiz_session(
                                                conv["id"], quizzes.compute_quiz_type(items), payload
                                            )
                                        # Recorded in full, not summarized - this is
                                        # exactly the artifact needed to permanently
                                        # diagnose a malformed generated quiz the
                                        # defensive per-item validation in
                                        # quizRenderers.js/quizDragDrop.js): every
                                        # quiz payload the tutor ever generates is now
                                        # queryable in Langfuse, whether or not the
                                        # student happened to notice a bad slide.
                                        with observability.span(
                                            "tool_call:start_quiz",
                                            input=payload,
                                            metadata={"reused_in_progress": existing is not None, "quiz_id": quiz_id},
                                            session_id=conv["id"],
                                            user_id=profile.get("id"),
                                        ):
                                            pass
                                        quiz_state["active"] = True
                                        quiz_state["quiz_id"] = quiz_id
                                        await websocket.send_json(
                                            {
                                                "type": "quiz_start",
                                                "quiz_id": quiz_id,
                                                "items": payload.get("items"),
                                            }
                                        )
                                    function_responses.append(
                                        types.FunctionResponse(
                                            id=fc.id,
                                            name=fc.name,
                                            response={"result": "ok"},
                                        )
                                    )
                                await _live_session.send_tool_response(function_responses=function_responses)

                            resumption_update = getattr(response, "session_resumption_update", None)
                            if resumption_update is not None:
                                # Logged unconditionally (not just the actionable
                                # branch below) - this is the only way to see
                                # whether Google is sending these at all, since
                                # "always shows fresh session" could mean either
                                # "never arrives" or "arrives but resumable=False"/
                                # no new_handle, and those need different fixes.
                                print(
                                    f"[ws_session] session_resumption_update: resumable={getattr(resumption_update, 'resumable', None)} has_new_handle={getattr(resumption_update, 'new_handle', None) is not None}"
                                )
                            if resumption_update and getattr(resumption_update, "resumable", False):
                                new_handle = getattr(resumption_update, "new_handle", None)
                                if conv is not None and new_handle:
                                    print(f"[ws_session] storing new resumption handle for conversation={conv['id']!r}")
                                    memory.set_resumption(conv["id"], new_handle, _config_identity)

                            if sc and getattr(sc, "turn_complete", False):
                                await flush_turn_buffer_to_memory(_current_turn_chunks, _hf_turn_chunks)
                                await websocket.send_json({"type": "turn_complete"})

                tasks = [
                    asyncio.create_task(browser_to_live()),
                    asyncio.create_task(live_to_browser()),
                ]
                done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                for task in pending:
                    task.cancel()
                session_error_1011 = None
                for task in done:
                    if getattr(task, "cancelled", lambda: False)():
                        continue
                    exc = task.exception()
                    if exc is not None and not isinstance(exc, WebSocketDisconnect):
                        if "1011" in str(exc):
                            session_error_1011 = exc
                        else:
                            print(f"[ws_session] task raised: {type(exc).__name__}: {exc}")

                if session_error_1011 is not None and fallback_model:
                    prev_model = model_name
                    print(f"[ws_session] 1011 error - switching to fallback model: {fallback_model}")
                    try:
                        await live_cm.__aexit__(None, None, None)
                    except Exception as exc:  # noqa: BLE001
                        print(f"[ws_session] live_cm.__aexit__ failed: {exc}")
                    model_name = fallback_model
                    fallback_model = prev_model
                    if conv is not None:
                        conv_config["model_name"] = model_name
                        config_identity = dict(conv_config)
                        memory.update_conversation(conv["id"], config=conv_config)
                    config = build_config(
                        profile,
                        conv_config,
                        model_name,
                        resumption_handle=resumption_handle,
                        summary_text=summary_text,
                        review_terms=review_terms,
                    )
                    (
                        live_cm,
                        live_session,
                        handle_was_dropped,
                    ) = await _connect_live_with_retries(client, model_name, config)
                    if handle_was_dropped and conv is not None:
                        memory.clear_resumption(conv["id"])
                        resumption_handle = None
                    resumed = resumption_handle is not None and not handle_was_dropped
                    hf_state["turn_active"] = False  # the new Gemini session has no open turn from the old one
                    if current_turn_chunks:
                        try:
                            await live_session.send_realtime_input(activity_start=types.ActivityStart())
                            for chunk in current_turn_chunks:
                                await live_session.send_realtime_input(
                                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                                )
                            await live_session.send_realtime_input(activity_end=types.ActivityEnd())
                            print(f"[ws_session] replayed {len(current_turn_chunks)} buffered audio chunks to fallback model")
                        except Exception as replay_exc:  # noqa: BLE001
                            print(f"[ws_session] audio replay to fallback model failed: {replay_exc}")
                        current_turn_chunks.clear()
                    if hf_turn_chunks:
                        # Same idea as current_turn_chunks above, for a hands-free
                        # turn that was mid-flight when the 1011 hit. Unlike
                        # push-to-talk, nothing else needs to happen after this -
                        # hf_state["turn_active"] is already False (reset just
                        # above), so the very next accepted window opens a fresh
                        # turn on its own; there's no button for the person to
                        # re-click.
                        try:
                            await live_session.send_realtime_input(activity_start=types.ActivityStart())
                            for chunk in hf_turn_chunks:
                                await live_session.send_realtime_input(
                                    audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                                )
                            await live_session.send_realtime_input(activity_end=types.ActivityEnd())
                            print(f"[ws_session] replayed {len(hf_turn_chunks)} buffered hands-free windows to fallback model")
                        except Exception as replay_exc:  # noqa: BLE001
                            print(f"[ws_session] hands-free audio replay to fallback model failed: {replay_exc}")
                        hf_turn_chunks.clear()
                    try:
                        await websocket.send_json(
                            {
                                "type": "session_status",
                                "resumed": resumed,
                                "conversation_name": (conv or {}).get("name"),
                                "model_name": model_name,
                            }
                        )
                    except Exception as exc:  # noqa: BLE001
                        print(f"[ws_session] send_json failed: {exc}")
                    continue
                break
            except WebSocketDisconnect:
                break
            except Exception as e:  # noqa: BLE001
                print(f"[ws_session] outer exception: {type(e).__name__}: {e}")
                try:
                    await websocket.send_json(_connect_failure_payload(e, str(e)))
                except (WebSocketDisconnect, RuntimeError) as exc:
                    print(f"[ws_session] send_json failed: {exc}")
                break
    finally:
        # current_turn_chunks/hf_turn_chunks are always bound by the time we
        # reach here - this finally sits after the while loop, which always
        # runs its body at least once before any path that reaches this
        # point.
        await flush_turn_buffer_to_memory(current_turn_chunks, hf_turn_chunks)
        if conv is not None:
            # Final fold on disconnect - catches the tail so a session that
            # ends mid-way through a summarization interval isn't lost, and
            # is cheap/best-effort like every other summarization call.
            try:
                await asyncio.to_thread(
                    summarize_conversation,
                    conv["id"],
                    profile.get("name") or "the student",
                    profile.get("api_key"),
                )
            except Exception as exc:  # noqa: BLE001
                print(f"[ws_session] summarize_conversation failed: {exc}")
        if profile.get("id"):
            # Best-effort, same posture as summarize_conversation just above -
            # re-fetches the profile fresh rather than trusting the one held
            # in this function's closure the whole session, since a long
            # session could easily outlive that snapshot (e.g. the person
            # edits their name mid-session via Settings) and this must only
            # ever ADD to total_seconds_studied, never overwrite it with a
            # stale total.
            try:
                elapsed_seconds = int(time.monotonic() - session_start_monotonic)
                if elapsed_seconds > 0:
                    fresh = get_profile_by_id(profile["id"]) or profile
                    patch_profile(
                        profile["id"],
                        {"total_seconds_studied": (fresh.get("total_seconds_studied") or 0) + elapsed_seconds},
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[ws_session] total_seconds_studied update failed: {exc}")
        try:
            await live_cm.__aexit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            print(f"[ws_session] live_cm.__aexit__ failed: {exc}")
        try:
            await websocket.close()
        except Exception as exc:  # noqa: BLE001
            print(f"[ws_session] websocket.close failed: {exc}")
