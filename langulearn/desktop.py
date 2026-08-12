"""
Desktop wrapper - runs the FastAPI server in a background thread and opens
it in a native app window via pywebview, instead of a browser tab. Called
from cli.py's `langulearn` command (see run() below); the __main__ block
exists only for running this file directly against a live source checkout
during development (`python -m langulearn.desktop`).

Notes:
- private_mode=False keeps the WebView2 profile (cookies, permissions like
  microphone access) persistent across app launches. pywebview defaults to
  an ephemeral profile, which is why the mic permission prompt was
  reappearing on every restart.
- Window size is set generously wide, since the sidebar + main layout needs
  real horizontal room - it's resizable, so this is just a sane default.
- icon is a webview.start()-level setting (applies to the app/window icon,
  not per-window) - support varies a bit by platform/pywebview version, so
  if it doesn't show up in the title bar/taskbar, that's a version quirk to
  look into rather than a sign something else is broken.
"""

import sys
import threading
import time
from pathlib import Path

import uvicorn
import webview

from .main import app  # reuse the exact same FastAPI app defined in main.py

# pywebview's icon= param wants a .ico on Windows and a .icns on macOS (per
# its own docs) - a bare .ico silently does nothing on Mac rather than
# erroring, which would otherwise look like a mysterious missing icon
# rather than the platform mismatch it actually is.
_ICON_NAME = "LanguLearn.icns" if sys.platform == "darwin" else "LanguLearn.ico"
ICON_PATH = Path(__file__).parent / "static" / _ICON_NAME


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Starts the FastAPI server in a background thread, then opens the
    desktop window pointed at it. Blocks until the window is closed
    (webview.start() is blocking) - this is the actual entry point cli.py's
    `langulearn` command calls after the first-run bootstrap (if any) has
    already completed.
    """
    webview.settings["ALLOW_DOWNLOADS"] = True  # off by default in pywebview - without this, downloads silently do nothing

    def start_server():
        uvicorn.run(app, host=host, port=port, log_level="info")

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Give uvicorn a moment to bind before pointing the window at it.
    time.sleep(1.5)

    # Window size is set generously wide, since the sidebar + main layout
    # needs real horizontal room - it's resizable, so this is just a sane
    # default. Bumped from an earlier 1200/900 baseline: with the quiz
    # drawer (420-480px) added alongside the sidebar, that width left the
    # chat area cramped enough to feel like the drawer was overlapping it
    # rather than sharing the window (see design_plans/issues_fix.md #3) -
    # this isn't a confirmed complete fix, since the exact conditions
    # under which the overlap was seen weren't fully reproduced, but it's
    # a real, verified contributing factor worth having regardless.
    webview.create_window(
        "LanguLearn",
        # A cache-busting query string forces a fresh fetch on every launch,
        # regardless of anything already sitting in the persistent WebView2
        # profile's cache (see the private_mode=False note above).
        #
        # Points at /profiles rather than "/" (the learning page) - every
        # app launch should land on the profile picker, like a Netflix
        # "who's watching" screen, rather than silently resuming whatever
        # profile happened to be active last time.
        f"http://{host}:{port}/profiles?v={int(time.time())}",
        width=1360,
        height=860,
        min_size=(1100, 700),
        resizable=True,
    )
    webview.start(private_mode=False, icon=str(ICON_PATH) if ICON_PATH.exists() else None)


if __name__ == "__main__":
    run()
