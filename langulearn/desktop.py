"""
Desktop wrapper - runs the FastAPI server in a background thread and opens
it in a native app window via pywebview, instead of a browser tab. Called
from cli.py's `langulearn` command (see run() below); the __main__ block
exists only for running this file directly against a live source checkout
during development (`python -m langulearn.desktop`).

Notes:
- private_mode=False keeps the WebView2 profile (cookies, permissions like
  microphone access) persistent across app launches. pywebview defaults to
  an ephemeral profile.
- Window size is set generously wide, since the sidebar + main layout needs
  real horizontal room - it's resizable, so this is just a sane default.
- icon is a webview.start()-level setting (applies to the app/window icon,
  not per-window) - support varies a bit by platform/pywebview version, so
  if it doesn't show up in the title bar/taskbar, that's a version quirk to
  look into rather than a sign something else is broken.
"""

import socket
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


def _wait_for_port_free(host: str, port: int, timeout: float = 10.0) -> bool:
    """Polls whether (host, port) can actually be bound, returning True as
    soon as it can (immediately, on a normal launch where nothing else is
    using it). Exists specifically for the Update & Relaunch race: the new
    process can start trying to bind before the OLD process - still mid-
    shutdown via close_this_window() - has released the port (see
    design_plans/issues.md; relaunch.log showed "WinError 10048: only one
    usage of each socket address..."). uvicorn itself swallows a bind
    failure silently - logs it, then returns normally rather than raising
    (see Server.startup() catching the OSError) - so run() below had no
    way to notice anything went wrong and opened the window regardless.
    Waiting for the port to be genuinely free before ever starting uvicorn
    sidesteps that entirely, rather than trying to detect/recover from a
    failure uvicorn already hid.

    Deliberately does NOT set SO_REUSEADDR on the probe socket - on
    Windows that can let a bind "succeed" while another socket is still
    actively listening on the same address, which is exactly the false
    positive this needs to avoid.
    """
    deadline = time.monotonic() + timeout
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind((host, port))
                return True
            except OSError:
                pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.3)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Starts the FastAPI server in a background thread, then opens the
    desktop window pointed at it. Blocks until the window is closed
    (webview.start() is blocking) - this is the actual entry point cli.py's
    `langulearn` command calls after the first-run bootstrap (if any) has
    already completed.
    """
    webview.settings["ALLOW_DOWNLOADS"] = True  # off by default in pywebview - without this, downloads silently do nothing

    if not _wait_for_port_free(host, port):
        print(f"[desktop] {host}:{port} still in use after waiting - starting anyway, uvicorn will report the real error")

    def start_server():
        uvicorn.run(app, host=host, port=port, log_level="info")

    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Give uvicorn a moment to bind before pointing the window at it.
    time.sleep(1.5)

    # Window size is set generously wide, since the sidebar + main layout
    webview.create_window(
        "LanguLearn",
        # A cache-busting query string forces a fresh fetch on every launch,
        # regardless of anything already sitting in the persistent WebView2
        # profile's cache (see the private_mode=False note above).
        #
        # Points at /landing homepage on every launch - the last-active profile stays "logged
        # in" regardless (see localStorage.tutorProfileId, read by
        # profileMenu.js's top-bar button on every page)
        f"http://{host}:{port}/landing?v={int(time.time())}",
        width=1360,
        height=860,
        min_size=(1100, 700),
        resizable=True,
    )
    webview.start(
        private_mode=False,
        icon=str(ICON_PATH) if ICON_PATH.exists() else None,
        debug=False,  # Turn on for debugging.
    )


if __name__ == "__main__":
    run()
