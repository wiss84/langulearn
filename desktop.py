"""
Desktop wrapper - runs the FastAPI server in a background thread and opens
it in a native app window via pywebview, instead of a browser tab. Same
pattern as Local Search Agent's desktop UI.

Run:
    python desktop.py

Note: pywebview on Windows uses the Edge WebView2 runtime, which does
support microphone access (getUserMedia) - if the mic permission prompt
doesn't appear or access is denied, that's the first thing to check.
"""

import threading
import time

import uvicorn
import webview

from main import app  # reuse the exact same FastAPI app defined in main.py

HOST = "127.0.0.1"
PORT = 8000


def start_server():
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    server_thread = threading.Thread(target=start_server, daemon=True)
    server_thread.start()

    # Give uvicorn a moment to bind before pointing the window at it.
    time.sleep(1.5)

    webview.create_window(
        "AI Polish Tutor",
        f"http://{HOST}:{PORT}/",
        width=480,
        height=760,
        resizable=True,
    )
    webview.start()
