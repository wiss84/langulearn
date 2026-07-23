"""Static configuration: prompt templates, tool declarations, voice/model
option lists, defaults, and file paths shared across the app. No logic
lives here - just data other modules import.
"""

from pathlib import Path

from google.genai import types

# Shared across every scenario (see the scenarios/ package) - a scenario
# module only holds its persona/setting text; this correction mandate and
# native-language fallback behavior apply identically regardless of setting,
# so it's appended after the scenario template rather than duplicated in
# each of the scenario files. {name}/{native_language}/{target_language}
# filled in the same way as the scenario template itself.
CORE_TUTOR_RULES = (
    "\n\nYou are {tutor_name}, an {target_language} tutor. Always introduce yourself as {tutor_name} when meeting {name} for the first time.\n\n"
    "CORE RULES YOU MUST FOLLOW:\n"
    "Always correct mistakes in {target_language}. If {name} "
    "makes ANY error (grammar, vocabulary, pronunciation, verb form, etc.), do "
    "NOT praise the attempt. If unsure, treat it as incorrect and ask them to try "
    "again.\n\n"
    "For every mistake:\n"
    "1. Say in {native_language} that it was incorrect.\n"
    "2. Give the correct version.\n"
    "3. Ask {name} to repeat it.\n"
    "4. Keep correcting and asking until they say it correctly, or after 3 genuine "
    "attempts. After the 3rd failed attempt, give the correct version again, say "
    "it's okay to continue, and move on.\n"
    "5. Never continue to a new topic before a correct repetition or the 3-attempt limit.\n\n"
    "When {name} speaks in {native_language}, reply in {native_language}, introduce "
    "the natural {target_language} equivalent, and briefly explain why it fits.\n\n"
    "Keep replies short, interactive, and conversational, but never skip required corrections."
    "Always Keep the conversation going."
)

# Also shared across every scenario, appended right after CORE_TUTOR_RULES.
# Same {name}/{native_language}/{target_language} placeholders.
DIFFICULTY_INSTRUCTIONS = {
    "beginner": (
        "\n\nDifficulty: beginner. Use simple, short sentences and common "
        "vocabulary. Be patient - if {name} is stuck, switch to {native_language} "
        "briefly to help them, then bring them back to {target_language}."
    ),
    "intermediate": (
        "\n\nDifficulty: intermediate. Use natural conversational pace and "
        "everyday vocabulary. Still correct every mistake per the rules above, "
        "but expect {name} to keep up without much hand-holding."
    ),
    "advanced": (
        "\n\nDifficulty: advanced. Speak at a natural native pace, using "
        "idiomatic phrasing and varied vocabulary. Minimal simplification - "
        "treat {name} like a capable speaker who is refining fluency."
    ),
}
DEFAULT_DIFFICULTY = "intermediate"


# Appended to the system instruction only when a conversation is starting a
# fresh Live session (no valid resumption handle) and has a stored rolling
# summary - re-seeds context Google's own session state can no longer carry.
MEMORY_CONTEXT_TEMPLATE = (
    "\n\nContext remembered from earlier conversations with {name} (use this "
    "naturally to stay consistent - don't recite it verbatim or announce that "
    "you're reading notes):\n{summary}"
)

# Appended to every system instruction, unconditionally - drives the
# set_mood tool call handled in live_session.py (see design_plans/ for the
# full design). The tool's own JSON schema (MOOD_TOOL) stays mechanical
# (valid values only); all the "why/when to pick each one" guidance lives
# here instead, so tuning the tutor's expressive behavior only ever means
# editing this prose block.
#
# 'sleep' is deliberately NOT in the enum - it's a separate, deterministic
# client-side idle-timeout state (see armIdleSleepTimer in audio.js), not
# something Gemini should ever be able to trigger mid-conversation.
MOOD_INSTRUCTION = (
    "\n\nYou have a mandatory set_mood tool. Call it silently on EVERY reply; "
    "never mention or narrate it.\n\n"
    "Use exactly these moods:\n"
    "- happy: student gets it right on the first try.\n"
    "- sad: correcting any mistake.\n"
    "- fear: 2nd or 3rd incorrect repetition.\n"
    "- love: corrected phrase finally repeated correctly.\n"
    "- neutral: new topic or any other case.\n\n"
    "Always call set_mood once per response; use neutral if no other mood applies."
)

MOOD_TOOL = types.Tool(
    function_declarations=[
        types.FunctionDeclaration(
            name="set_mood",
            description=(
                "Silently express the tutor's emotional reaction to the "
                "current moment in the conversation, so the avatar's face "
                "reflects it."
            ),
            parameters=types.Schema(
                type="OBJECT",
                properties={
                    "mood": types.Schema(
                        type="STRING",
                        enum=["neutral", "happy", "sad", "fear", "love"],
                    )
                },
                required=["mood"],
            ),
        )
    ]
)

DEFAULT_VOICE = "Kore"
DEFAULT_NATIVE_LANGUAGE = "English"
DEFAULT_TARGET_LANGUAGE = "Polish"

# Non-realtime text model used only for periodic rolling-summary folding
# (memory.py / summarize_conversation in summarization.py) - cheap
# free-tier text calls, separate from the Live API models used for the
# actual conversation. gemini-2.5-flash's free tier is capped at 20 RPD,
# too low for a summarization call firing every ~15 turns across active
# conversations; gemini-3.1-flash-lite gives 500 RPD instead and is plenty
# for this task.
SUMMARY_MODEL = "gemini-3.1-flash-lite"

# Official voice names + descriptors are from Google's docs
# (ai.google.dev/gemini-api/docs/speech-generation), which label voices by
# tone/character. Gender is not an official Google label - this mapping is
# a manual categorization for the UI's gender-first picker, not a claim
# Google makes. pitch is a supplementary manual categorization (same
# spirit as gender), shown alongside descriptor on the avatar-select page.
VOICE_OPTIONS = [
    {"name": "Zephyr", "descriptor": "Bright", "gender": "Female", "pitch": "Mid-range", "alias": "Mila"},
    {"name": "Puck", "descriptor": "Upbeat", "gender": "Male", "pitch": "Mid-range", "alias": "Max"},
    {"name": "Charon", "descriptor": "Informative", "gender": "Male", "pitch": "Mid-to-low", "alias": "Leo"},
    {"name": "Kore", "descriptor": "Firm", "gender": "Female", "pitch": "Mid-to-high", "alias": "Chloe"},
    {"name": "Fenrir", "descriptor": "Excitable", "gender": "Male", "pitch": "Mid-range", "alias": "Felix"},
    {"name": "Leda", "descriptor": "Youthful", "gender": "Female", "pitch": "High", "alias": "Ava"},
    {"name": "Orus", "descriptor": "Firm", "gender": "Male", "pitch": "Mid-to-low", "alias": "Miles"},
    {"name": "Aoede", "descriptor": "Breezy", "gender": "Female", "pitch": "Mid-range", "alias": "Zoe"},
    {"name": "Callirrhoe", "descriptor": "Easy-going", "gender": "Female", "pitch": "Mid-to-high", "alias": "Elena"},
    {"name": "Autonoe", "descriptor": "Bright", "gender": "Female", "pitch": "High", "alias": "Maya"},
    {"name": "Enceladus", "descriptor": "Breathy", "gender": "Male", "pitch": "Mid-range", "alias": "Hugo"},
    {"name": "Iapetus", "descriptor": "Clear", "gender": "Male", "pitch": "Mid-to-low", "alias": "Jasper"},
    {"name": "Umbriel", "descriptor": "Easy-going", "gender": "Male", "pitch": "Mid-range", "alias": "Oscar"},
    {"name": "Algieba", "descriptor": "Smooth", "gender": "Female", "pitch": "Mid-to-low", "alias": "Isla"},
    {"name": "Despina", "descriptor": "Smooth", "gender": "Female", "pitch": "Mid-range", "alias": "Nina"},
    {"name": "Erinome", "descriptor": "Clear", "gender": "Female", "pitch": "Mid-to-high", "alias": "Lana"},
    {"name": "Algenib", "descriptor": "Gravelly", "gender": "Male", "pitch": "Low", "alias": "Theo"},
    {"name": "Rasalgethi", "descriptor": "Informative", "gender": "Male", "pitch": "Mid-range", "alias": "Milo"},
    {"name": "Laomedeia", "descriptor": "Upbeat", "gender": "Female", "pitch": "Mid-range", "alias": "Jade"},
    {"name": "Achernar", "descriptor": "Soft", "gender": "Female", "pitch": "Mid-range", "alias": "Ruby"},
    {"name": "Alnilam", "descriptor": "Firm", "gender": "Male", "pitch": "Mid-to-low", "alias": "Ezra"},
    {"name": "Schedar", "descriptor": "Even", "gender": "Male", "pitch": "Mid-to-low", "alias": "Kai"},
    {"name": "Gacrux", "descriptor": "Mature", "gender": "Female", "pitch": "Mid-to-low", "alias": "Holly"},
    {"name": "Pulcherrima", "descriptor": "Forward", "gender": "Female", "pitch": "High", "alias": "Stella"},
    {"name": "Achird", "descriptor": "Friendly", "gender": "Male", "pitch": "Mid-to-high", "alias": "Finn"},
    {"name": "Zubenelgenubi", "descriptor": "Casual", "gender": "Male", "pitch": "Low", "alias": "Nico"},
    {"name": "Vindemiatrix", "descriptor": "Gentle", "gender": "Female", "pitch": "Low", "alias": "Wren"},
    {"name": "Sadachbia", "descriptor": "Lively", "gender": "Male", "pitch": "Low", "alias": "Dean"},
    {"name": "Sadaltager", "descriptor": "Knowledgeable", "gender": "Male", "pitch": "Mid-range", "alias": "Brett"},
    {"name": "Sulafat", "descriptor": "Warm", "gender": "Female", "pitch": "Mid-to-high", "alias": "Piper"},
]

# Live API model choices. Rate limits are what's visible on the free tier as
# of mid-2026 and can change - shown in the UI so the choice is informed.
# Only the 2.5 native-audio generation exposes "affective dialog" (emotional
# tone sensitivity/expression); the 3.x line traded that for lower latency.
MODEL_OPTIONS = [
    {
        "id": "gemini-2.5-flash-native-audio-latest",
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
# retry.call_with_retry in summarization.py - this loop stays separate only
# because it manages an async context manager (see _connect_live_with_retries
# in live_session.py).
RETRY_ATTEMPTS = 4  # additional attempts after the first, so 5 tries total
RETRY_BASE_DELAY = 1.5  # seconds, doubles each retry (or uses Google's suggested retryDelay if present)
