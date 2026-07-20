"""
One-time (per voice) script: generates a short spoken sample for a Gemini
prebuilt voice and saves it as a .wav under static/voices/, so the avatar
lip-sync test page (static/avatar_test/test.html) can play a real voice
sample through the avatar without needing a live mic or a manually-supplied
audio file.

This uses Gemini's plain (non-Live) text-to-speech generation endpoint -
a single request/response call, completely separate from the Live API
session relay in main.py. Cheap, one-shot, not part of the app's runtime
conversation flow at all.

Usage (one voice):
    python scripts/generate_voice_sample.py Sulafat

Usage (custom text):
    python scripts/generate_voice_sample.py Sulafat --text "Hello there, I'm excited to help you practice today!"

Usage (every voice in VOICE_OPTIONS - main.py's full 30-voice list):
    python scripts/generate_voice_sample.py --all

Output lands at static/voices/<VoiceName>.wav (24kHz mono 16-bit PCM,
per Gemini TTS's fixed output format), overwriting any existing file for
that voice.
"""

import argparse
import sys
import wave
from pathlib import Path

# Reuses main.py's existing client/API key/voice list as the single source
# of truth, rather than duplicating the 30-voice list here and risking it
# drifting out of sync. Importing main.py does execute its module-level
# code (FastAPI app creation, static file mount, DB init) as a side effect -
# harmless here since this script never runs the app, just imports the
# objects it needs. Run from the project root, same as main.py itself.
sys.path.insert(0, str(Path(__file__).parent.parent))
from main import VOICE_OPTIONS, client  # noqa: E402

from google.genai import types  # noqa: E402

TTS_MODEL = "gemini-2.5-flash-preview-tts"
OUTPUT_DIR = Path(__file__).parent.parent / "static" / "voices"

DEFAULT_TEXT = (
    "Say warmly and conversationally: Hi there! I'm really looking forward "
    "to practicing with you today. Ready to get started?"
)


def generate_sample(voice_name: str, text: str) -> Path:
    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name)
                )
            ),
        ),
    )
    pcm_bytes = response.candidates[0].content.parts[0].inline_data.data

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"{voice_name}.wav"
    with wave.open(str(out_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(24000)  # Gemini TTS's fixed output rate
        wf.writeframes(pcm_bytes)
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("voice", nargs="?", help="A voice name from VOICE_OPTIONS in main.py, e.g. Sulafat")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text for the voice to speak (only used for a single voice)")
    parser.add_argument("--all", action="store_true", help="Generate a sample for every voice in VOICE_OPTIONS")
    args = parser.parse_args()

    if args.all:
        print(f"Generating {len(VOICE_OPTIONS)} voice samples...\n")
        for v in VOICE_OPTIONS:
            name = v["name"]
            try:
                out_path = generate_sample(name, DEFAULT_TEXT)
                print(f"  {name}: OK -> {out_path}")
            except Exception as e:
                print(f"  {name}: FAILED - {type(e).__name__}: {e}")
        return

    if not args.voice:
        parser.error("Provide a voice name, or use --all")

    valid_names = {v["name"] for v in VOICE_OPTIONS}
    if args.voice not in valid_names:
        parser.error(f"{args.voice!r} isn't in VOICE_OPTIONS. Valid names: {sorted(valid_names)}")

    out_path = generate_sample(args.voice, args.text)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
