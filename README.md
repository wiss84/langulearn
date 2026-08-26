# ThirtyTutors

**An AI language tutor that talks with you, remembers you, and has a face.**

![ThirtyTutors AI tutor conversation demo](https://raw.githubusercontent.com/wiss84/thirtytutors/main/thirtytutors/static/images/demo.gif)

Self-hosted. Open-source. Powered by Gemini Live API.

---

## What is this?

ThirtyTutors is a real-time voice conversation partner for learning a language, not a flashcard app and not a text chatbot. You hold down a key, speak, and a 3D avatar answers back out loud — in the language you're learning, correcting your mistakes, remembering what you've covered, and reacting with actual facial expressions while it talks.

It runs entirely on your own machine, using your own free Gemini API key. Nothing about your conversations goes anywhere except directly to Google's Gemini Live API — there's no backend service, no account, no analytics, no middleman.

```
You hold to talk → speak → Gemini Live API (audio in, audio out) → avatar lip-syncs the reply
                                        ↓
                     every turn quietly saved, summarized, and fed back in later
                     so the tutor still remembers you after a reconnect
```

---

## Demo Videos

- **0.3.0 Release** — Watch the full [ThirtyTutors UI-UX](https://youtu.be/o4WNlLXm4qo)
- **0.2.0 Release** — Watch the full [ThirtyTutors Quiz System](https://youtu.be/9d4UrbZbwfU)
- **0.1.0  Initial Release** — Watch the full [ThirtyTutors Tutorial](https://youtu.be/0YEskMeH4Gk)

---

## Why this instead of a text chatbot?

Most "AI language tutors" tools are really just a chat window with a system prompt. That's fine for grammar explanations, but it doesn't train the thing that actually makes a language hard to speak: real-time listening and speaking under mild pressure, and actually correcting you as you go.

ThirtyTutors is built around the Gemini **Live API** specifically because it's full-duplex audio — you talk, it listens and replies, in real time, the same shape as an actual conversation. On top of that:

- **It corrects you, every time.** The tutor is instructed not to let mistakes slide — wrong grammar, vocabulary, or pronunciation gets caught, explained in your native language, and drilled until you get it right.
- **It has a face.** A 3D avatar lip-syncs to the reply and reacts with mood-appropriate expressions (encouraging, sympathetic, proud) — driven silently by the model itself, not a canned animation loop.
- **Hands-free Mode.** Enroll your voice once, and the tutor filters out background noise and other people's voices automatically — no push-to-talk needed if you don't want it.
- **It doesn't forget you.** Conversations persist across sessions with a rolling memory summary, so reconnecting doesn't mean starting over.

---

## Screenshots

### Create a new profile
![Create Profile](https://raw.githubusercontent.com/wiss84/thirtytutors/main/thirtytutors/static/images/home.webp)

### Login to existing profile
![Profile Page](https://raw.githubusercontent.com/wiss84/thirtytutors/main/thirtytutors/static/images/Create_profile.webp)

### Avatar & voice selection
![Profile Page](https://raw.githubusercontent.com/wiss84/thirtytutors/main/thirtytutors/static/images/Existing_profile.webp)

### Learning Session Page
![Profile Page](https://raw.githubusercontent.com/wiss84/thirtytutors/main/thirtytutors/static/images/Pick_Tutor.webp)

### Learning Session (Full Screen) Page
![Profile Page](https://raw.githubusercontent.com/wiss84/thirtytutors/main/thirtytutors/static/images/Session.webp)

---

## Key Features

- **Real-time spoken conversation** — full-duplex audio via the Gemini Live API, not turn-based text
- **3D talking avatar** — lip-synced, ARKit-blendshape facial animation, pick from 30 distinct voices/personas
- **Mood-reactive expressions** — the avatar's face changes based on how the conversation is going, driven silently by the model
- **Hands-free mode** — enroll your voice once and the tutor listens continuously, filtering out background noise and other speakers via on-device speaker verification (nothing about your voiceprint ever leaves your machine)
- **Persistent memory** — every conversation gets a rolling summary, tracked vocabulary, and a recurring-mistakes log, so a reconnect (or a restart) doesn't mean starting from zero
- **In-conversation quizzes** — the tutor can quiz you mid-conversation with multiple-choice or drag-and-drop questions, pulling from vocabulary you've actually gotten wrong before, without ever interrupting the conversation itself
- **Test Yourself** — replay every quiz item from a whole conversation as one standalone round, any time, filtered by date range and deduplicated - a quick way to check what's actually sticking without waiting for the tutor to bring it up
- **Stats & milestones** — a day streak, hours studied, and a per-language breakdown of words mastered, quiz accuracy, and perfect-quiz badges, with a notification the moment you cross a milestone
- **Profile backup** — export one profile's full conversation/quiz history and voice enrollment as a single zip, and import it back later; optional scheduled automatic backups too
- **Multiple profiles, multiple languages** — each profile can run several conversations at once, each with its own voice, language pair, difficulty, and scenario
- **Home page & profile switching** — the app opens on a real home page every launch (your last-active profile stays logged in automatically), with a permanent profile-switcher icon and a profile menu for Settings/switching from anywhere
- **Roleplay scenarios** — free conversation, ordering at a café, checking in at an airport, asking for directions, and more
- **Three difficulty levels** — beginner, intermediate, advanced, adjustable per conversation
- **Automatic model fallback** — if one Gemini model is unavailable, ThirtyTutors transparently retries on a second one
- **Export your notes** — print or export a conversation's vocabulary/mistake log to Word
- **Built-in update notifications** — a bell in the top bar lets you know when a new app version or a refreshed avatar/voice library is available, with a one-click update-and-relaunch
- **100% self-hosted** — your own API key, your data stays on your machine, no cloud service in between (Except for Google, but you can opt-out via Account's Gemini Apps Activity page).

---

## Requirements

- Python 3.11+
- A free [Google AI Studio](https://aistudio.google.com/apikey) API key (Gemini's free tier is enough to use this)
- A microphone and speakers
- Windows or macOS (Linux likely works too — untested so far)

---

## Setup

A virtual environment is recommended, same as any Python package:
```bash
conda create -n thirtytutors python=3.11 -y
conda activate thirtytutors
pip install thirtytutors
thirtytutors setup          # install extras + download assets + create shortcut, without launching
```

That's it. The first run does a one-time setup automatically: installs the two extra packages hands-free mode needs, downloads the avatar/voice/photo assets (~372MB) and the landing page's video/gif assets (~84MB) — 455MB total, so this part takes a couple of minutes — and creates a desktop shortcut for you — then opens the app. Every run after that just opens the app straight away, no repeated setup, whether you launch it via `thirtytutors` again or the new desktop shortcut.

No `.env` file or API key setup needed - you'll paste your own free Gemini API key directly into the app the first time you create a profile.

Want more control over the one-time setup, or need to re-run it (e.g. after a broken install)?

```bash
thirtytutors setup --force  # same, but re-downloads assets even if already up to date
thirtytutors --host 0.0.0.0 --port 8080   # override the default 127.0.0.1:8000
```

## Run it

After the first-run setup, just use the desktop shortcut it created, or run `thirtytutors` again from a terminal.

Prefer a browser tab over the desktop window? `thirtytutors` always opens as a native app window - if you'd rather run it as a plain local web server instead, clone the repo and run `uvicorn thirtytutors.main:app --reload --port 8000` against a source checkout (see Contributing below), then open `http://127.0.0.1:8000/`.

---

## How it works, in a bit more detail

1. **Profiles** hold your identity, mic preference, and API key. One machine can have several profiles.
2. **Conversations** live under a profile — each one is a language + voice + difficulty + scenario combination, with its own memory. Switch between them freely; only one is ever live at a time.
3. Every conversation turn is transcribed by Gemini itself and saved locally. Periodically (and on disconnect), a background pass folds recent turns into a short rolling summary and pulls out notable vocabulary or recurring mistakes.
4. When a session reconnects — hitting a session time limit, or reopening the app — ThirtyTutors tries to resume the exact same Gemini session first. If that's not possible, it starts a fresh one and quietly re-seeds it with the rolling summary, so the tutor doesn't act like it's meeting you for the first time.
5. **Hands-free mode** enrolls a short voice sample per profile, then filters incoming audio through a speaker-verification pass before anything reaches Gemini — so it only responds to you, not a TV in the background or someone else talking.
6. **Quizzes** can be triggered by the tutor at natural points in the conversation — multiple choice or drag-and-drop word bank, right in a side drawer without losing your place. Anything you get wrong feeds into a per-profile mistakes log, so those words are more likely to come back around in a future quiz.

---

## Limitations

- **The displayed transcript of what you said is sometimes wrong, even when the tutor's reply isn't.** Gemini Live API produces the on-screen transcript of your speech through a separate speech-to-text pass from the one the model actually listens with — so the tutor often responds correctly to what you actually said while the text shown for it is garbled, off-topic, or barely related. This is a Gemini Live API characteristic, not something ThirtyTutors's own audio pipeline can control or fix.

---

## Staying up to date

ThirtyTutors checks for updates automatically when it opens, and periodically while it's running - both a new app version and a refreshed avatar/voice/photo library. When one's available, a bell icon in the top bar shows it; click it, then the notification, for details and a one-click **Update & Relaunch**. You can also check manually any time from the profile menu, or from Settings → Updates.

---

## Profile & Data


Avatar `.glb` models, voice `.wav` samples, and tile `.webp` photos are downloaded separately on first run (see Setup above) rather than bundled in the package - they're too large (~450MB) to ship in a normal pip install. Your profiles, conversations, voice enrollment data, and those downloaded assets all live in an OS-managed per-user data directory, not inside a repo checkout.

---

## Contributing

Issues and pull requests are welcome. For local development:

```bash
git clone https://github.com/wiss84/thirtytutors.git
cd thirtytutors
conda create -n thirtytutors python=3.11 -y
conda activate thirtytutors
pip install -e ".[dev]"
thirtytutors setup   # one-time: extra deps + assets + shortcut
```

`pip install -e .` means the `thirtytutors` command runs directly against your live source tree - no separate build/reinstall step needed while iterating.

There isn't a formal contribution guide yet, so when in doubt, keep changes focused, run `pytest` before opening a PR, and describe what you tested manually for anything touching the frontend or the Live API relay (some of it - real-time audio, the actual avatar rendering - isn't practical to cover with automated tests).

```bash
cd thirtytutors
pytest tests/ -v --cov --cov-report=term-missing
ruff check .
ruff check . --fix # Fix any issues found before a PR
ruff check .
ruff format . # format before a PR
```

---

## License

[PolyForm Noncommercial License 1.0.0](LICENSE) — free to use, modify, fork, and share for any noncommercial purpose (personal use, learning, research, hobby projects). Commercial use requires a separate agreement — reach out if that's what you're after.

---

Built by [Wissam Metawee](https://github.com/wiss84)
