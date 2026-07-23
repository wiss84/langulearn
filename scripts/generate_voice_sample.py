"""
One-time (per voice) script: generates a unique spoken sample for each Gemini
prebuilt voice and saves it as a .wav under static/voices/, so the avatar
lip-sync test page (static/avatar_test/test.html) can play a real voice
sample through the avatar without needing a live mic or a manually-supplied
audio file.

This uses Gemini's plain (non-Live) text-to-speech generation endpoint.

Usage (one voice):
    python scripts/generate_voice_sample.py Sulafat

Usage (custom text override):
    python scripts/generate_voice_sample.py Sulafat --text "Hello there!"

Usage (every voice in VOICE_OPTIONS - generates 30 unique greetings):
    python scripts/generate_voice_sample.py --all
"""

import argparse
import sys
import wave
from pathlib import Path

# Ensures root imports work correctly
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import the main client object to keep API authentication synchronized
try:
    from main import client
except ImportError:
    # Fallback configuration if imported directly outside the FastAPI environment
    from google.genai import Client
    client = Client()

from google.genai import types

TTS_MODEL = "gemini-2.5-flash-preview-tts"
OUTPUT_DIR = Path(__file__).parent.parent / "static" / "voices"

# Comprehensive 30-voice registry containing name, tone descriptor, pitch profile, 
# and a unique language-learning greeting phrase mapped explicitly to each option.
VOICE_OPTIONS = [
    {
        "name": "Puck", "tone": "Upbeat", "pitch": "Mid-range",
        "text": "Hey there! Ready to shake off the rust and jump into some vocabulary practice today? Let's do this!"
    },
    {
        "name": "Charon", "tone": "Informative", "pitch": "Mid-to-low",
        "text": "Welcome back. Today we'll cover real-world conversational phrases to help you speak more naturally."
    },
    {
        "name": "Kore", "tone": "Firm", "pitch": "Mid-to-high",
        "text": "Let's focus on clear pronunciation today. Listen closely to my accent and try to match it!"
    },
    {
        "name": "Fenrir", "tone": "Excitable", "pitch": "Mid-range",
        "text": "Awesome to see you! I've got some great new idioms lined up for us today. Are you ready?"
    },
    {
        "name": "Aoede", "tone": "Breezy", "pitch": "Mid-range",
        "text": "Hi! Let's take it easy today and just practice a relaxed, everyday conversation. Tell me about your week."
    },
    {
        "name": "Zephyr", "tone": "Bright", "pitch": "Mid-range",
        "text": "Hello! I am so excited to help you practice your language skills today. What should we learn first?"
    },
    {
        "name": "Orus", "tone": "Firm", "pitch": "Mid-to-low",
        "text": "Consistent practice is the key to fluency. Let's begin today's lesson with a quick review."
    },
    {
        "name": "Autonoe", "tone": "Bright", "pitch": "High",
        "text": "Hi friend! Don't worry about making mistakes today, that's exactly how we learn and improve!"
    },
    {
        "name": "Umbriel", "tone": "Easy-going", "pitch": "Mid-range",
        "text": "Hey, let's keep things casual today. We can just chat about your favorite hobbies and interests."
    },
    {
        "name": "Erinome", "tone": "Clear", "pitch": "Mid-to-high",
        "text": "Greetings. Today's goal is to sharpen your listening comprehension. Let me know if I should slow down."
    },
    {
        "name": "Laomedeia", "tone": "Upbeat", "pitch": "Mid-range",
        "text": "Hey! Time to level up your speaking confidence. I'll guide you through it every step of the way."
    },
    {
        "name": "Schedar", "tone": "Even", "pitch": "Mid-to-low",
        "text": "Welcome. We will work through our grammar exercises at whatever pace feels most comfortable for you."
    },
    {
        "name": "Achird", "tone": "Friendly", "pitch": "Mid-to-high",
        "text": "Hi there! I'm really looking forward to practicing with you today. Ready to get started?"
    },
    {
        "name": "Sadachbia", "tone": "Lively", "pitch": "Low",
        "text": "Hey, glad you made it! Let's dive right into some high-energy dialogue practice and test your speed."
    },
    {
        "name": "Enceladus", "tone": "Breathy", "pitch": "Mid-range",
        "text": "Hello. Let's focus on the subtle rhythms of speech today to help you sound much more native."
    },
    {
        "name": "Algieba", "tone": "Smooth", "pitch": "Mid-to-low",
        "text": "Welcome back. Take a deep breath, relax, and let's practice your sentence flow together."
    },
    {
        "name": "Algenib", "tone": "Gravelly", "pitch": "Low",
        "text": "Let's cut right to the chase today. We're breaking down advanced listening exercises."
    },
    {
        "name": "Achernar", "tone": "Soft", "pitch": "Mid-range",
        "text": "Hello there. I'm here to give you a supportive space to practice. Let's try some simple phrasing."
    },
    {
        "name": "Gacrux", "tone": "Mature", "pitch": "Mid-to-low",
        "text": "Good day. Language learning is a journey, and I am honored to help you guide your speech patterns."
    },
    {
        "name": "Zubenelgenubi", "tone": "Casual", "pitch": "Low",
        "text": "Hey, what's up? Let's skip the textbook stuff today and practice real slang you'll actually use."
    },
    {
        "name": "Sadaltager", "tone": "Knowledgeable", "pitch": "Mid-range",
        "text": "Welcome. If you have any questions about complex sentence structures, I'm fully prepared to break them down."
    },
    {
        "name": "Leda", "tone": "Youthful", "pitch": "High",
        "text": "Hi there! Let's play a language game today to make practicing our verbs way more fun."
    },
    {
        "name": "Callirrhoe", "tone": "Easy-going", "pitch": "Mid-to-high",
        "text": "Hey! No pressure today, just continuous talking practice to help you overcome any hesitation."
    },
    {
        "name": "Iapetus", "tone": "Clear", "pitch": "Mid-to-low",
        "text": "Pronunciation clarity is our priority today. Let's carefully break down these tricky vowel sounds."
    },
    {
        "name": "Despina", "tone": "Smooth", "pitch": "Mid-range",
        "text": "Hi. Let's focus on conversational pacing today so you feel confident speaking with anyone."
    },
    {
        "name": "Rasalgethi", "tone": "Informative", "pitch": "Mid-range",
        "text": "Hello. Today we will unpack situational dialogue, specifically focusing on navigating a new city."
    },
    {
        "name": "Alnilam", "tone": "Firm", "pitch": "Mid-to-low",
        "text": "Let's stick to our target schedule today and push your speaking boundaries to the next level."
    },
    {
        "name": "Sulafat", "tone": "Warm", "pitch": "Mid-to-high",
        "text": "Hello! I am absolutely delighted to see your progress. Let's keep that momentum going today."
    },
    {
        "name": "Vindemiatrix", "tone": "Calm", "pitch": "Low",
        "text": "Welcome. Let's move mindfully through your exercises today, focusing on structural accuracy."
    },
    {
        "name": "Pulcherrima", "tone": "Energetic", "pitch": "High",
        "text": "Yay, you're back! Let's crush this language session and learn some awesome expressions today!"
    }
]


def generate_sample(voice_name: str, text: str) -> Path:
    # Formats the generation prompt to guide Gemini's affective dialogue rendering
    prompt_text = f"Say warmly and conversationally: {text}"
    
    response = client.models.generate_content(
        model=TTS_MODEL,
        contents=prompt_text,
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
        wf.setsampwidth(2)      # 16-bit PCM
        wf.setframerate(24000)  # Gemini TTS's fixed native rate
        wf.writeframes(pcm_bytes)
    return out_path


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("voice", nargs="?", help="A voice name from the internal registry, e.g. Sulafat")
    parser.add_argument("--text", default=None, help="Optional text override for a single voice generation turn")
    parser.add_argument("--all", action="store_true", help="Generate a unique sample for all 30 voices sequentially")
    args = parser.parse_args()

    # Dynamic lookup map to easily isolate specific voices
    voice_map = {v["name"]: v for v in VOICE_OPTIONS}

    if args.all:
        print(f"Generating {len(VOICE_OPTIONS)} unique voice samples...\n")
        for v in VOICE_OPTIONS:
            name = v["name"]
            text_to_speak = v["text"]
            try:
                out_path = generate_sample(name, text_to_speak)
                print(f"  {name} ({v['tone']}, {v['pitch']}): OK -> {out_path}")
            except Exception as e:
                print(f"  {name}: FAILED - {type(e).__name__}: {e}")
        return

    if not args.voice:
        parser.error("Provide a voice name, or use --all")

    if args.voice not in voice_map:
        parser.error(f"'{args.voice}' isn't in valid options. Valid options: {sorted(voice_map.keys())}")

    # Use explicitly passed custom text override, otherwise fall back to the mapped unique phrase
    chosen_voice = voice_map[args.voice]
    text_to_speak = args.text if args.text is not None else chosen_voice["text"]

    print(f"Generating for {chosen_voice['name']} ({chosen_voice['tone']}, {chosen_voice['pitch']})...")
    out_path = generate_sample(chosen_voice["name"], text_to_speak)
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()