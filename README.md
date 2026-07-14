# AI Language Tutor - Phase 2/3 setup

## Setup

```
conda create -n ai-tutor python=3.11 -y
conda activate ai-tutor
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` and paste your (regenerated) AI Studio API key into `GEMINI_API_KEY`.

## Run

Browser version (two terminals):
```
uvicorn main:app --reload --port 8000
```
Then open `http://127.0.0.1:8000/`.

Desktop app version (one terminal, or double-click `AI-Tutor.bat`):
```
python desktop.py
```

## Manual diagnostic scripts (`scripts/`)

These aren't automated tests - they hit the live Gemini API and need a
real mic/speakers, so a human has to judge the result. Kept separate from
a future `tests/` folder, which will hold actual pytest-style tests once
there's logic worth unit-testing with mocks (e.g. the WebSocket relay).

- `scripts/test_live_api.py` - confirms the API key + model connect at all
  (text-only, no audio). Useful if something breaks after a key rotation
  or a `google-genai` version bump.
- `scripts/test_client.py` - records your mic directly and plays back a
  reply, bypassing the browser UI. Useful for isolating "is this a backend
  problem or a browser problem" when debugging.

## If something breaks

This uses preview Gemini Live API fields (`realtime_input_config`,
`session_resumption`, `activity_start`/`activity_end`, etc.) that can shift
between `google-genai` SDK versions. If you get an `AttributeError` on any
`types.*` call in `main.py`, that's a version mismatch, not a logic bug -
paste the exact error and we'll adjust the field name. Server-side errors
also get printed to the terminal and shown in the browser UI directly.
