# LanguLearn - setup

## Setup

```
conda create -n ai-tutor python=3.11 -y
conda activate ai-tutor
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and paste your AI Studio API key into `GEMINI_API_KEY`.

## Run

Browser version:
```
uvicorn main:app --reload --port 8000
```
Then open `http://127.0.0.1:8000/`.

Desktop app version (one terminal, or double-click `AI-Tutor.bat`):
```
python desktop.py
```

## Profiles

On first run you'll be asked to create a profile (just a name). A profile is
just an identity + microphone choice, stored server-side in
`data/profiles.json` (gitignored - personal data, not project source). The
profile's name is what shows up in the transcript. Switch profiles anytime
via the button in the top bar.

Voice, model, and language pair used to live on the profile - they now live
on each **conversation** instead (see below), since a profile can hold
several conversations with different settings at once.

## Conversations & memory

Each profile can hold multiple conversations, listed in the sidebar. Every
conversation has its own voice, gender, language pair, and model, and its
own memory - switching conversations is like switching which notebook
you're writing in.

- **New conversation**: set voice/languages/model in the sidebar first (a
  new conversation is created with whatever's currently selected), then hit
  "+ New conversation".
- **Switch**: click a conversation in the list. This closes the current Live
  API connection and opens (or resumes) that conversation's own session -
  only one is ever connected at a time.
- **Rename** (pencil icon) / **delete** (×) work like the profile list does.
- Whichever conversation is active is remembered across an app restart.

**Changing voice, model, or either language reconnects** the Live API
session for the current conversation - these are fixed for the lifetime of a
session, so a brief reconnect is expected when you change one. That's always
been true; what's new is what happens next: instead of a cold start, the
tutor gets re-seeded with what it remembers about that conversation (see
below), so context isn't lost just because you switched voices.

**Persisted memory.** Every transcribed turn is saved to `data/memory.db`
(SQLite, gitignored) per conversation - nothing is capped or deleted short of
explicitly deleting the conversation. Roughly every 15 turns (and once more
when a session ends), a background call folds recent turns into a short
rolling summary, and pulls out any notable vocabulary or recurring mistakes
along the way. Whenever a conversation starts a **fresh** Google session (no
resumable handle, or an expired one), that summary is quietly folded into
the system instruction so the tutor still has context instead of a cold
start. A small indicator next to the connection dot in the top bar shows
"resumed session" vs. "fresh session" for a few seconds after each connect,
so it's visible which one happened.

The notes icon (📝) next to "+ New conversation" opens a read-only view of the
current conversation's tracked vocabulary/recurring mistakes and its lesson
log - both are side effects of the same rolling-summary pass, not something
you edit directly.

If you used this app before conversations existed, your old profile-level
session is automatically wrapped into a "Default" conversation the first
time you load that profile - nothing is lost.

## Transcription quality

The tutor's own spoken replies are transcribed by Gemini itself and shown as
they stream in - that's reliably good, so it's used as-is.

The student's side is **not** taken from Gemini's own live transcription.
That side-channel of the Live API is noticeably weaker than the model's
actual audio understanding, which is why the tutor's replies stay accurate
even when what showed up as "what you said" was garbled or unrelated.
Instead, the same raw mic audio already being streamed to Gemini is also
kept locally and run through `faster-whisper` (`large-v3-turbo`, 99+
languages) the moment your turn ends - off the live audio path, so it
doesn't slow the conversation down; the tutor's spoken reply almost always
takes longer than this does anyway. You'll see a brief italic "…" placeholder
bubble appear the instant you release the talk button, replaced a moment
later with the actual transcription once it's ready.

**First run after installing this**: the first time you speak, the app
downloads the `large-v3-turbo` weights (~1.6GB) from Hugging Face into
`data/whisper_cache/` (gitignored, same as the rest of `data/`) and loads
them - this one-time step can take a minute or two depending on your
connection and CPU. Every turn after that loads from the local cache with
no network call at all. Runs CPU-only (int8-quantized), no GPU required.

If `faster-whisper` isn't installed yet (i.e. you haven't run
`pip install -r requirements.txt` since this was added) or the first-run
download fails, the student's side just comes back empty rather than
crashing the session - the tutor's replies and the rest of the app are
unaffected either way.

## Project layout

```
main.py                   FastAPI backend: Live API relay, profile/conversation REST API
memory.py                 SQLite layer: conversations, turns, rolling summaries, vocab/mistakes, lesson log
retry.py                  Shared retry/backoff helper for transient Gemini API errors
transcribe.py             Local speech-to-text (faster-whisper) for the student's side of the transcript
static/index.html         Page structure
static/style.css          Design tokens + layout
static/app.js             All frontend logic (profiles, conversations, mic, voice, waveform, WS relay)
static/pcm-processor.js   AudioWorklet mic capture processor
data/profiles.json        Per-profile identity + mic + active conversation (gitignored)
data/memory.db            Per-conversation config, transcripts, summaries, notes (gitignored)
data/whisper_cache/       Locally cached faster-whisper model weights (gitignored)
scripts/                  Manual diagnostic tools (see below)
```

## Manual diagnostic scripts (`scripts/`)

These aren't automated tests - they hit the live Gemini API and need a real
mic/speakers, so a human has to judge the result. Kept separate from a
future `tests/` folder, reserved for actual pytest-style tests once there's
logic worth unit-testing with mocks.

- `scripts/test_live_api.py` - confirms the API key + model connect at all
  (text-only, no audio). Useful after a key rotation or SDK version bump.
- `scripts/test_client.py` - records your mic directly and plays back a
  reply, bypassing the browser UI. Useful for isolating backend vs. browser
  issues.

## If something breaks

This uses preview Gemini Live API fields (`realtime_input_config`,
`session_resumption`, `activity_start`/`activity_end`, `speech_config`,
`enable_affective_dialog`, etc.) that can shift between `google-genai` SDK
versions. An `AttributeError`/`TypeError`/`ValueError` on a `types.*` call
in `main.py` is most likely a version/API-surface mismatch, not a logic bug.

AI Studio's free tier throws a fair number of transient `500 INTERNAL` /
`503 UNAVAILABLE` / `429` errors under normal use - `retry.py` retries these
automatically (exponential backoff, or Google's own suggested `retryDelay`
when a 429 includes one) for both the Live API connection and the
background summarization call, so a lot of these resolve themselves without
any visible interruption. What you'll actually see in the terminal is a
line per retry attempt (`[ws_session] connect attempt N failed ... -
retrying in Xs...` or `[summarize_conversation/<id>] attempt N/M failed ...`)
- that's expected and not itself a bug. Only once every attempt is
exhausted does it surface: a connect failure shows up in the app's red
error banner, while a summarization failure is silent (logged only) since
it just retries again at the next scheduled fold and nothing is lost in
the meantime - all turns stay in `data/memory.db` regardless.
