"""Tests for desktop.py's run() - not the actual GUI window (opening a real
pywebview window isn't practical in an automated test), but the settings
and call sequence around it, which is exactly the kind of thing that can
silently regress without anyone noticing until a manual test.
"""

import pytest

from langulearn import desktop

pytestmark = pytest.mark.unit


def test_run_enables_downloads_and_opens_a_window(monkeypatch):
    calls = []

    monkeypatch.setattr(desktop.uvicorn, "run", lambda *a, **k: None)
    monkeypatch.setattr(desktop.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(desktop.webview, "create_window", lambda *a, **k: calls.append(("create_window", a, k)))
    monkeypatch.setattr(desktop.webview, "start", lambda *a, **k: calls.append(("start", a, k)))
    desktop.webview.settings["ALLOW_DOWNLOADS"] = False

    desktop.run(host="127.0.0.1", port=8000)

    assert desktop.webview.settings["ALLOW_DOWNLOADS"] is True
    assert [c[0] for c in calls] == ["create_window", "start"]

    _, args, _ = calls[0]
    assert "127.0.0.1:8000" in args[1]
