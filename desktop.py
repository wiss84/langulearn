"""
Desktop wrapper - runs the FastAPI server in a background thread and opens
it in a native app window via pywebview, instead of a browser tab.

Run:
    python desktop.py

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

import threading
import time
from pathlib import Path

import uvicorn
import webview

from backend.main import app  # reuse the exact same FastAPI app defined in main.py

HOST = "127.0.0.1"
PORT = 8000
ICON_PATH = Path(__file__).parent / "static" / "LanguLearn.ico"


def start_server():
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Give uvicorn a moment to bind before pointing the window at it.
    time.sleep(1.5)

    webview.create_window(
        "LanguLearn",
        # A cache-busting query string forces a fresh fetch on every launch,
        # regardless of anything already sitting in the persistent WebView2
        # profile's cache (see the private_mode=False note above - that
        # persistence is what let a stale index.html get served with no
        # request even reaching the server, silently missing whatever
        # markup was added since). The server's own no-cache headers (see
        # main.py's _disable_static_caching middleware) stop this from
        # recurring going forward, but that only prevents *future* caching -
        # it doesn't invalidate anything already cached before that
        # middleware existed, which is what this query string is for.
        #
        # Points at /profiles rather than "/" (the learning page) - every
        # app launch should land on the profile picker, like a Netflix
        # "who's watching" screen, rather than silently resuming whatever
        # profile happened to be active last time.
        f"http://{HOST}:{PORT}/profiles?v={int(time.time())}",
        width=1200,
        height=820,
        min_size=(900, 650),
        resizable=True,
    )
    webview.start(private_mode=False, icon=str(ICON_PATH) if ICON_PATH.exists() else None)
