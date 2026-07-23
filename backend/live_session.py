"""Gemini Live API relay: config building, connect-retry handling, and the
/ws/session websocket route. Split out of main.py.

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

Mood: the tutor's avatar expression is driven two ways (see design_plans/
for the full design) - a mood_change message forwarded here whenever Gemini
calls the set_mood tool (silent, model's discretion, driven by
constants.MOOD_INSTRUCTION), and a client-side-only idle-timeout 'sleep'
state the frontend manages entirely on its own (armIdleSleepTimer in
audio.js) - this server never sends or knows about 'sleep'.

Retry behavior: Gemini's preview models occasionally return a transient
"500 INTERNAL", "503 UNAVAILABLE", or (less often) a 429 rate-limit error
that succeeds if you just try again - see retry.py for the shared
classification/backoff logic. The initial Live session connection retries
up to RETRY_ATTEMPTS additional times with exponential backoff (or Google's
own suggested retryDelay when present) before giving up. Errors that aren't
recognizably transient (bad request, auth, unknown model, etc.) fail
immediately instead of being retried pointlessly.
"""

import asyncio
import base64

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types

from . import memory, retry, scenarios
from .constants import (
    CORE_TUTOR_RULES,
    DEFAULT_DIFFICULTY,
    DEFAULT_MODEL,
    DEFAULT_NATIVE_LANGUAGE,
    DEFAULT_TARGET_LANGUAGE,
    DEFAULT_VOICE,
    DIFFICULTY_INSTRUCTIONS,
    MEMORY_CONTEXT_TEMPLATE,
    MODEL_OPTIONS,
    MOOD_INSTRUCTION,
    MOOD_TOOL,
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY,
    VOICE_OPTIONS,
)
from .profiles_store import get_client_for_key, get_profile_by_id, patch_profile
from .summarization import summarize_conversation

router = APIRouter()


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
) -> types.LiveConnectConfig:
    name = profile.get("name") or "the student"
    native_language = conv_config.get("native_language") or DEFAULT_NATIVE_LANGUAGE
    target_language = conv_config.get("target_language") or DEFAULT_TARGET_LANGUAGE
    voice_name = conv_config.get("voice_name") or DEFAULT_VOICE
    tutor_name = next((v.get("alias") or v["name"] for v in VOICE_OPTIONS if v["name"] == voice_name), voice_name)
    fmt_kwargs = dict(name=name, native_language=native_language, target_language=target_language, tutor_name=tutor_name)

    scenario_id = conv_config.get("scenario") or scenarios.DEFAULT_SCENARIO
    scenario_template = scenarios.SCENARIO_TEMPLATES.get(scenario_id, scenarios.SCENARIO_TEMPLATES[scenarios.DEFAULT_SCENARIO])
    difficulty = conv_config.get("difficulty") or DEFAULT_DIFFICULTY
    difficulty_instruction = DIFFICULTY_INSTRUCTIONS.get(difficulty, DIFFICULTY_INSTRUCTIONS[DEFAULT_DIFFICULTY])

    system_instruction = (
        scenario_template.format(**fmt_kwargs)
        + CORE_TUTOR_RULES.format(**fmt_kwargs)
        + difficulty_instruction.format(**fmt_kwargs)
    )
    if summary_text:
        system_instruction += MEMORY_CONTEXT_TEMPLATE.format(name=name, summary=summary_text)
    system_instruction += MOOD_INSTRUCTION

    kwargs = dict(
        response_modalities=["AUDIO"],
        system_instruction=system_instruction,
        tools=[MOOD_TOOL],
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

    # Computed once, up front, so it covers BOTH failure paths that need a
    # fallback: this initial connect attempt below, and the mid-session
    # 1011 handling further down. Previously only the mid-session path got
    # a fallback attempt - a model that was already down at the very start
    # of a session just failed outright instead of trying the other one,
    # which also meant the "both models unavailable" lamp state (see
    # constants.MODEL_OPTIONS / the top-bar model indicator) could never
    # actually reflect a same-session hard failure at connect time.
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
    except Exception as first_exc:
        if fallback_model:
            print(f"[ws_session] initial connect to {model_name!r} failed ({type(first_exc).__name__}) - trying fallback model {fallback_model!r} before giving up.")
            prev_model, model_name = model_name, fallback_model
            fallback_model = prev_model
            if conv is not None:
                conv_config["model_name"] = model_name
                config_identity = dict(conv_config)
                memory.update_conversation(conv["id"], config=conv_config)
            config = build_config(profile, conv_config, model_name, resumption_handle=resumption_handle, summary_text=summary_text)
            try:
                live_cm, live_session, handle_was_dropped = await _connect_live_with_retries(client, model_name, config)
                if handle_was_dropped and conv is not None:
                    memory.clear_resumption(conv["id"])
                    resumption_handle = None
            except Exception as second_exc:
                print(f"[ws_session] fallback connect to {model_name!r} also failed ({type(second_exc).__name__}) - no models available.")
                try:
                    await websocket.send_json({
                        "type": "error",
                        "message": f"Could not connect to either configured model: {type(second_exc).__name__}: {second_exc}",
                    })
                    await websocket.send_json({"type": "session_status", "model_name": None, "unavailable": True})
                except Exception:
                    pass
                await websocket.close()
                return
        else:
            print(f"[ws_session] connect failed after retries: {type(first_exc).__name__}: {first_exc}")
            try:
                await websocket.send_json({
                    "type": "error",
                    "message": f"Could not connect to the tutor after retrying: {type(first_exc).__name__}: {first_exc}",
                })
                await websocket.send_json({"type": "session_status", "model_name": None, "unavailable": True})
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
            "model_name": model_name,
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
    
        while True:
            current_turn_chunks = []
            try:
                async def browser_to_live():
                    while True:
                        msg = await websocket.receive_json()
                        msg_type = msg.get("type")

                        try:
                            if msg_type == "start_turn":
                                current_turn_chunks.clear()
                                await live_session.send_realtime_input(activity_start=types.ActivityStart())
                            elif msg_type == "audio_chunk":
                                pcm_bytes = base64.b64decode(msg["data"])
                                current_turn_chunks.append(pcm_bytes)
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
                    go_away_pending = False
                    while True:
                        async for response in live_session.receive():
                            sc = getattr(response, "server_content", None)
                            go_away = getattr(response, "go_away", None)
                            if go_away is not None:
                                go_away_pending = True
                                print(f"[ws_session] go_away received: {go_away}")
                                continue

                            if go_away_pending and sc and getattr(sc, "turn_complete", False):
                                go_away_pending = False
                                await flush_turn_buffer_to_memory()
                                await websocket.send_json({"type": "turn_complete"})
                                return

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
    
                            tool_call = getattr(response, "tool_call", None)
                            if tool_call:
                                function_responses = []
                                for fc in tool_call.function_calls:
                                    if fc.name == "set_mood":
                                        mood = (fc.args or {}).get("mood", "neutral")
                                        await websocket.send_json({"type": "mood_change", "mood": mood})
                                    function_responses.append(
                                        types.FunctionResponse(id=fc.id, name=fc.name, response={"result": "ok"})
                                    )
                                await live_session.send_tool_response(function_responses=function_responses)
    
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

                if session_error_1011 is not None:
                    if fallback_model:
                        prev_model = model_name
                        print(f"[ws_session] 1011 error - switching to fallback model: {fallback_model}")
                        try:
                            await live_cm.__aexit__(None, None, None)
                        except Exception:
                            pass
                        model_name = fallback_model
                        fallback_model = prev_model
                        if conv is not None:
                            conv_config["model_name"] = model_name
                            config_identity = dict(conv_config)
                            memory.update_conversation(conv["id"], config=conv_config)
                        config = build_config(profile, conv_config, model_name, resumption_handle=resumption_handle, summary_text=summary_text)
                        live_cm, live_session, handle_was_dropped = await _connect_live_with_retries(client, model_name, config)
                        if handle_was_dropped and conv is not None:
                            memory.clear_resumption(conv["id"])
                            resumption_handle = None
                        resumed = resumption_handle is not None and not handle_was_dropped
                        if current_turn_chunks:
                            try:
                                await live_session.send_realtime_input(activity_start=types.ActivityStart())
                                for chunk in current_turn_chunks:
                                    await live_session.send_realtime_input(
                                        audio=types.Blob(data=chunk, mime_type="audio/pcm;rate=16000")
                                    )
                                await live_session.send_realtime_input(activity_end=types.ActivityEnd())
                                print(f"[ws_session] replayed {len(current_turn_chunks)} buffered audio chunks to fallback model")
                            except Exception as replay_exc:
                                print(f"[ws_session] audio replay to fallback model failed: {replay_exc}")
                            current_turn_chunks.clear()
                        try:
                            await websocket.send_json({
                                "type": "session_status",
                                "resumed": resumed,
                                "conversation_name": (conv or {}).get("name"),
                                "model_name": model_name,
                            })
                        except Exception:
                            pass
                        continue
                break
            except WebSocketDisconnect:
                break
            except Exception as e:
                print(f"[ws_session] outer exception: {type(e).__name__}: {e}")
                try:
                    await websocket.send_json({"type": "error", "message": str(e)})
                except Exception:
                    pass
                break
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
