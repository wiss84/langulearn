"""
Phase 2 test client - no browser needed yet. Records a few seconds from your
mic, streams it to the local FastAPI WebSocket as 16kHz/16-bit/mono PCM chunks,
signals turn_complete, then prints transcripts and saves the reply audio to a
.wav file you can double-click to play.

This is the "does the whole loop work" test before we build the actual browser UI.

Run (with main.py already running in another terminal via `uvicorn main:app --reload`):
    python test_client.py
"""

import asyncio
import base64
import json
import wave

import numpy as np
import sounddevice as sd
import websockets

SERVER_URL = "ws://127.0.0.1:8000/ws/session"
SAMPLE_RATE = 16000  # required input rate for the Live API
CHANNELS = 1
RECORD_SECONDS = 5
CHUNK_SIZE_BYTES = 3200  # ~100ms of 16-bit mono 16kHz audio per chunk


def record_audio() -> bytes:
    print(f"Recording for {RECORD_SECONDS}s - speak now (English or Polish)...")
    audio = sd.rec(int(RECORD_SECONDS * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=CHANNELS, dtype="int16")
    sd.wait()
    print("Recording done, sending to server...")
    return audio.tobytes()


async def main():
    pcm_bytes = record_audio()

    async with websockets.connect(SERVER_URL) as ws:
        for i in range(0, len(pcm_bytes), CHUNK_SIZE_BYTES):
            chunk = pcm_bytes[i:i + CHUNK_SIZE_BYTES]
            await ws.send(json.dumps({
                "type": "audio_chunk",
                "data": base64.b64encode(chunk).decode("ascii"),
            }))
        await ws.send(json.dumps({"type": "turn_complete"}))
        print("Sent. Waiting for the tutor's reply...")

        audio_out = bytearray()
        while True:
            raw = await ws.recv()
            msg = json.loads(raw)

            if msg["type"] == "audio":
                audio_out.extend(base64.b64decode(msg["data"]))
            elif msg["type"] == "transcript_in":
                print(f"Model heard you say: {msg['text']!r}")
            elif msg["type"] == "transcript_out":
                print(f"Tutor said: {msg['text']!r}")
            elif msg["type"] == "turn_complete":
                break
            elif msg["type"] == "error":
                print(f"ERROR from server: {msg['message']}")
                break

        if audio_out:
            out_path = "test_client_output.wav"
            with wave.open(out_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(24000)  # Live API audio output rate
                wf.writeframes(bytes(audio_out))
            print(f"Saved reply audio to {out_path} - double-click to play.")


if __name__ == "__main__":
    asyncio.run(main())
