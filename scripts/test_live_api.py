"""
Round 2: now that we know the connection itself works (no billing/permission
errors - it just rejected TEXT-only responses), we request AUDIO back instead,
since these are native-audio-dialog models that can only reply with audio.

This script:
    1. Connects using the two model names you can actually see in AI Studio.
    2. Sends a short text prompt asking for a Polish sentence.
    3. Collects the returned audio + the output transcript (so we get text too).
    4. Saves the audio to a .wav file so you can just double-click and listen -
       no need to wire up playback code yet.

Usage:
    pip install google-genai
    python test_live_api.py
"""

import asyncio
import os
import sys
import wave

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("Missing dependency. Run:  pip install google-genai")
    sys.exit(1)

API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    API_KEY = input("Paste your AI Studio API key (or press Enter to abort): ").strip()
    if not API_KEY:
        sys.exit(1)

# Only the models that actually connected last time (found, not "not found").
CANDIDATE_MODELS = [
    "gemini-2.5-flash-native-audio-preview-09-2025",
    "gemini-3.1-flash-live-preview",
]

# Gemini Live API audio output is 16-bit PCM, mono, 24kHz.
OUTPUT_SAMPLE_RATE = 24000

client = genai.Client(api_key=API_KEY)


async def try_model(model_name: str) -> bool:
    print(f"\n--- Trying model: {model_name} ---")
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )
    try:
        async with client.aio.live.connect(model=model_name, config=config) as session:
            print("Session opened. Sending a test message...")
            await session.send_client_content(
                turns=types.Content(
                    role="user",
                    parts=[types.Part(text="Say hello in Polish, one short sentence, speak slowly.")],
                ),
                turn_complete=True,
            )

            audio_chunks = bytearray()
            transcript_text = ""

            async for response in session.receive():
                if response.data:
                    audio_chunks.extend(response.data)
                sc = response.server_content
                if sc and sc.output_transcription and sc.output_transcription.text:
                    transcript_text += sc.output_transcription.text
                if sc and sc.turn_complete:
                    break

            print("CONNECTED OK.")
            print(f"  Transcript: {transcript_text!r}")
            print(f"  Audio bytes received: {len(audio_chunks)}")

            if audio_chunks:
                safe_name = model_name.replace("/", "_").replace(".", "_")
                out_path = f"test_output_{safe_name}.wav"
                with wave.open(out_path, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)  # 16-bit
                    wf.setframerate(OUTPUT_SAMPLE_RATE)
                    wf.writeframes(bytes(audio_chunks))
                print(f"  Saved audio to: {out_path}  (double-click to play)")

            return True
    except Exception as e:  # noqa: BLE001 - diagnostic script, we want to see everything
        msg = str(e)
        print(f"FAILED: {type(e).__name__}: {msg}")
        lowered = msg.lower()
        if "billing" in lowered or "permission" in lowered or "403" in msg:
            print("  -> This looks like a billing/permission block.")
        elif "429" in msg or "resource_exhausted" in lowered or "quota" in lowered:
            print("  -> Auth worked, just hit a free-tier rate limit.")
        elif "404" in msg or "not found" in lowered:
            print("  -> Model name not available for your account.")
        return False


async def main():
    any_success = False
    for model_name in CANDIDATE_MODELS:
        success = await try_model(model_name)
        any_success = any_success or success
    if any_success:
        print("\n=== RESULT: Live API works with your current free-tier key. ===")
    else:
        print("\n=== RESULT: still failing - see errors above. ===")


if __name__ == "__main__":
    asyncio.run(main())
