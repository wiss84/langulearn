"""
Phase 2/3: FastAPI backend that owns one Gemini Live API session per
WebSocket connection, relays audio/transcripts both ways, and serves the
browser UI (static/index.html).

Turn signaling (push-to-talk):
    client sends "start_turn"    -> server sends activity_start to Gemini
    client streams "audio_chunk" -> forwarded as realtime audio input
    client sends "turn_complete" -> server sends activity_end to Gemini

NOTE ON PREVIEW API SURFACE: field names here can shift between google-genai
SDK versions since this is a preview API. Every field read below uses getattr()
with a default instead of direct attribute access, so a wrong/missing field
name skips just that one piece of data instead of crashing the whole response
loop.

FIX IN THIS REVISION: live_session.receive() is a generator scoped to ONE
turn - it naturally ends once that turn's messages are exhausted. The
previous version called it once and let the whole session close afterward,
which is why the app disconnected after a single exchange. Now it loops and
calls receive() again for each subsequent turn, so the session stays open
for a full conversation. Task cancellation is also handled explicitly now,
so closing the tab doesn't leave an orphaned background task.

Also added language_codes hints to the transcription config, per Google's
best-practices docs, to reduce (not eliminate) input-transcription errors -
note the transcription is a separate, lighter process from the model's
actual audio understanding, so a wrong caption doesn't mean the model
misunderstood you.

Run:
    uvicorn main:app --reload --port 8000
Then open:
    http://127.0.0.1:8000/
"""

import asyncio
import base64
import os

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from google import genai
from google.genai import types

load_dotenv()

API_KEY = os.environ["GEMINI_API_KEY"]
MODEL = os.environ.get("TUTOR_MODEL", "gemini-2.5-flash-native-audio-preview-09-2025")

SYSTEM_INSTRUCTION = (
    "You are a friendly Polish language tutor speaking with an English-speaking "
    "student. Respond primarily in Polish, spoken slowly and clearly. When the "
    "student speaks English, treat it as them asking how to say something in "
    "Polish, and answer with the Polish phrase. When the student attempts "
    "Polish, gently correct pronunciation or grammar mistakes before "
    "continuing - briefly, don't lecture. Keep responses short, like a real "
    "spoken conversation, not an essay."
)

client = genai.Client(api_key=API_KEY)
app = FastAPI()

_last_resumption_handle: str | None = None


def build_config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=SYSTEM_INSTRUCTION,
        output_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US", "pl-PL"]),
        input_audio_transcription=types.AudioTranscriptionConfig(language_codes=["en-US", "pl-PL"]),
        realtime_input_config=types.RealtimeInputConfig(
            automatic_activity_detection=types.AutomaticActivityDetection(disabled=True)
        ),
        session_resumption=types.SessionResumptionConfig(handle=_last_resumption_handle),
    )


@app.websocket("/ws/session")
async def ws_session(websocket: WebSocket):
    global _last_resumption_handle
    await websocket.accept()
    config = build_config()

    try:
        async with client.aio.live.connect(model=MODEL, config=config) as live_session:

            async def browser_to_live():
                while True:
                    msg = await websocket.receive_json()
                    msg_type = msg.get("type")
                    print(f"[browser_to_live] received: {msg_type}")

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
                global _last_resumption_handle
                # Outer loop: receive() ends after each turn's messages are
                # exhausted, so we call it again to keep listening for the
                # next turn instead of letting the whole session end here.
                while True:
                    async for response in live_session.receive():
                        sc = getattr(response, "server_content", None)

                        if getattr(response, "data", None):
                            print(f"[live_to_browser] audio chunk: {len(response.data)} bytes")
                            await websocket.send_json({
                                "type": "audio",
                                "data": base64.b64encode(response.data).decode("ascii"),
                            })

                        out_transcript = getattr(sc, "output_transcription", None) if sc else None
                        out_text = getattr(out_transcript, "text", None) if out_transcript else None
                        if out_text:
                            print(f"[live_to_browser] transcript_out: {out_text!r}")
                            await websocket.send_json({"type": "transcript_out", "text": out_text})

                        in_transcript = getattr(sc, "input_transcription", None) if sc else None
                        in_text = getattr(in_transcript, "text", None) if in_transcript else None
                        if in_text:
                            print(f"[live_to_browser] transcript_in: {in_text!r}")
                            await websocket.send_json({"type": "transcript_in", "text": in_text})

                        # Lives on the TOP-LEVEL response, not on server_content.
                        resumption_update = getattr(response, "session_resumption_update", None)
                        if resumption_update and getattr(resumption_update, "resumable", False):
                            _last_resumption_handle = getattr(resumption_update, "new_handle", None)
                            print("[live_to_browser] session resumption handle updated")

                        if sc and getattr(sc, "turn_complete", False):
                            print("[live_to_browser] turn_complete")
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
        try:
            await websocket.close()
        except Exception:
            pass


app.mount("/", StaticFiles(directory="static", html=True), name="static")
