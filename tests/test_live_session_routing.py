"""Integration tests for /ws/session's message routing (live_session.py) -
the one explicitly called out as an example test target. Runs the real
route against a minimal FastAPI app, but with the Gemini client itself
faked out entirely (FakeClient/FakeLiveSession below) - nothing here
touches the network or needs a real API key.

Scope note: this covers the core push-to-talk turn flow, session-status
reporting, and resumption-handle storage. It does NOT cover hands-free
mode's own turn-boundary logic, the 1011 mid-session model-fallback path,
or dead-resumption-handle (1008) retry - those would need considerably
more elaborate fakes (a receive() that reacts to what was just sent, rather
than a fixed scripted batch) and are left for a follow-up rather than
rushed here.
"""

import asyncio
import base64
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from langulearn import live_session, memory

pytestmark = pytest.mark.integration


class FakeLiveSession:
    """Records everything sent to it, and yields a scripted batch of fake
    Gemini responses (once) from receive(). Every subsequent call to
    receive() blocks (awaiting an Event that's never set) instead of
    returning immediately - matching how the real API's receive() behaves
    while genuinely waiting for more data, and avoiding a busy-loop that
    would otherwise starve the event loop once the scripted responses run
    out (see the comment inside receive() below - this bit the first draft
    of this file for real, as a several-minutes-long apparent hang).
    """

    def __init__(self, responses=None):
        self._responses = responses or []
        self._served = False
        self._exhausted = asyncio.Event()
        self.sent_realtime_inputs = []
        self.sent_tool_responses = []

    async def send_realtime_input(self, **kwargs):
        self.sent_realtime_inputs.append(kwargs)

    async def send_tool_response(self, function_responses):
        self.sent_tool_responses.append(function_responses)

    async def receive(self):
        if not self._served:
            self._served = True
            for r in self._responses:
                yield r
        else:
            # Nothing left scripted. A plain `return` here would make the
            # outer `while True: async for response in live_session.receive():`
            # loop in live_to_browser call receive() again immediately, over
            # and over, with no actual suspension in between - a busy loop
            # that starves the event loop and hangs the whole test (this is
            # exactly what happened on first run). Awaiting an Event that's
            # never set is a real suspension instead: the task sits idle
            # until it's cancelled once browser_to_live finishes (the
            # asyncio.wait(..., FIRST_COMPLETED) + task.cancel() in
            # live_session.py's own loop), which is exactly how the real
            # API's receive() behaves while genuinely waiting for more data.
            await self._exhausted.wait()


class FakeLiveConnectCM:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc_info):
        return False


class FakeClient:
    """Stands in for genai.Client - only the .aio.live.connect(...) surface
    live_session.py actually uses."""

    def __init__(self, session):
        self.aio = SimpleNamespace(live=SimpleNamespace(connect=lambda model, config: FakeLiveConnectCM(session)))


def _server_content(input_text=None, output_text=None, turn_complete=False):
    return SimpleNamespace(
        input_transcription=SimpleNamespace(text=input_text) if input_text else None,
        output_transcription=SimpleNamespace(text=output_text) if output_text else None,
        turn_complete=turn_complete,
    )


def _response(
    server_content=None,
    data=None,
    go_away=None,
    tool_call=None,
    session_resumption_update=None,
):
    return SimpleNamespace(
        server_content=server_content,
        data=data,
        go_away=go_away,
        tool_call=tool_call,
        session_resumption_update=session_resumption_update,
    )


@pytest.fixture
def ws_app(monkeypatch):
    """Wires a FakeClient (with no scripted responses by default - tests
    override via the fake_session fixture below) into live_session.py and
    returns a TestClient for a minimal app exposing just /ws/session.
    Also stubs out summarize_conversation entirely: the code path that
    calls it on disconnect always runs regardless of turn count (see
    live_session.py's `finally` block), and a real call would try to reach
    Gemini for real with whatever fake API key the test profile has.
    """
    monkeypatch.setattr(live_session, "summarize_conversation", lambda *a, **k: None)

    def _make(fake_session: FakeLiveSession):
        monkeypatch.setattr(live_session, "get_client_for_key", lambda api_key: FakeClient(fake_session))
        app = FastAPI()
        app.include_router(live_session.router)
        return TestClient(app)

    return _make


def test_normal_turn_flow_routes_messages_and_persists_transcript(ws_app, make_profile, make_conversation):
    profile = make_profile(api_key="fake-key")
    conv = make_conversation(profile["id"], target_language="Spanish")

    fake_session = FakeLiveSession(
        responses=[
            _response(server_content=_server_content(input_text="hola")),
            _response(server_content=_server_content(output_text="\u00a1hola! \u00bfc\u00f3mo est\u00e1s?")),
            _response(server_content=_server_content(turn_complete=True)),
        ]
    )
    client = ws_app(fake_session)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(
            {
                "type": "init",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "conversation_id": conv["id"],
            }
        )

        status = ws.receive_json()
        assert status["type"] == "session_status"
        assert status["resumed"] is False  # brand-new conversation, no stored handle
        assert status["model_name"]

        assert ws.receive_json() == {"type": "transcript_in", "text": "hola"}
        assert ws.receive_json() == {
            "type": "transcript_out",
            "text": "\u00a1hola! \u00bfc\u00f3mo est\u00e1s?",
        }
        assert ws.receive_json() == {"type": "turn_complete"}

        # Drive the push-to-talk side - this is what actually exercises
        # browser_to_live's message routing into send_realtime_input.
        ws.send_json({"type": "start_turn"})
        audio_b64 = base64.b64encode(b"\x00\x01" * 50).decode()
        ws.send_json({"type": "audio_chunk", "data": audio_b64})
        ws.send_json({"type": "turn_complete"})
        ws.send_json({"type": "close"})

    # The scripted transcript was flushed to memory on turn_complete:
    turns = memory.get_turns(conv["id"])
    assert [(t["role"], t["text"]) for t in turns] == [
        ("user", "hola"),
        ("tutor", "\u00a1hola! \u00bfc\u00f3mo est\u00e1s?"),
    ]

    # And the push-to-talk sequence sent exactly activity_start -> audio -> activity_end:
    kinds = [next(iter(call.keys())) for call in fake_session.sent_realtime_inputs]
    assert kinds == ["activity_start", "audio", "activity_end"]
    assert fake_session.sent_realtime_inputs[1]["audio"].data == base64.b64decode(audio_b64)


def test_session_resumption_update_is_stored(ws_app, make_profile, make_conversation):
    profile = make_profile(api_key="fake-key")
    conv = make_conversation(profile["id"], target_language="Spanish")

    fake_session = FakeLiveSession(
        responses=[
            _response(session_resumption_update=SimpleNamespace(resumable=True, new_handle="fake-handle-123")),
            _response(server_content=_server_content(turn_complete=True)),
        ]
    )
    client = ws_app(fake_session)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(
            {
                "type": "init",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "conversation_id": conv["id"],
            }
        )
        ws.receive_json()  # session_status
        ws.receive_json()  # turn_complete
        ws.send_json({"type": "close"})

    stored = memory.get_conversation(conv["id"])
    assert stored["resumption_handle"] == "fake-handle-123"


def test_non_resumable_update_is_not_stored(ws_app, make_profile, make_conversation):
    profile = make_profile(api_key="fake-key")
    conv = make_conversation(profile["id"], target_language="Spanish")

    fake_session = FakeLiveSession(
        responses=[
            _response(session_resumption_update=SimpleNamespace(resumable=False, new_handle=None)),
            _response(server_content=_server_content(turn_complete=True)),
        ]
    )
    client = ws_app(fake_session)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(
            {
                "type": "init",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "conversation_id": conv["id"],
            }
        )
        ws.receive_json()
        ws.receive_json()
        ws.send_json({"type": "close"})

    assert memory.get_conversation(conv["id"])["resumption_handle"] is None


def test_reconnect_resumes_when_config_unchanged(ws_app, make_profile, make_conversation):
    profile = make_profile(api_key="fake-key")
    conv = make_conversation(profile["id"], target_language="Spanish")
    # Simulate a resumption handle stored by an earlier session with the
    # exact same config this conversation still has:
    memory.set_resumption(conv["id"], "handle-from-before", conv["config"])

    fake_session = FakeLiveSession(responses=[_response(server_content=_server_content(turn_complete=True))])
    client = ws_app(fake_session)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(
            {
                "type": "init",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "conversation_id": conv["id"],
            }
        )
        status = ws.receive_json()
        assert status["resumed"] is True
        ws.receive_json()
        ws.send_json({"type": "close"})


def test_init_must_be_the_first_message(ws_app, make_profile):
    client = ws_app(FakeLiveSession())
    with client.websocket_connect("/ws/session") as ws:
        ws.send_json({"type": "start_turn"})
        msg = ws.receive_json()
        assert msg["type"] == "error"


def test_profile_with_no_conversations_errors_cleanly(ws_app, make_profile):
    profile = make_profile(api_key="fake-key")  # no make_conversation call - zero conversations
    client = ws_app(FakeLiveSession())

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(
            {
                "type": "init",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "language" in msg["message"].lower()


def test_unknown_profile_id_falls_back_to_ephemeral_session(ws_app):
    """No persisted profile/conversation - config comes straight from the
    init message itself, and nothing gets written to memory.py (there's no
    conversation id to write against)."""
    fake_session = FakeLiveSession(responses=[_response(server_content=_server_content(turn_complete=True))])
    client = ws_app(fake_session)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(
            {
                "type": "init",
                "profile_id": "does-not-exist",
                "profile_name": "Guest",
                "target_language": "Spanish",
            }
        )
        status = ws.receive_json()
        assert status["type"] == "session_status"
        assert status["resumed"] is False
        ws.receive_json()  # turn_complete
        ws.send_json({"type": "close"})


def test_missing_api_key_reports_friendly_error(make_profile, make_conversation):
    """Doesn't use the ws_app fixture - deliberately exercises the real
    get_client_for_key (profiles_store.py), unmocked, against a profile
    with no API key at all.
    """
    profile = make_profile(api_key=None)
    conv = make_conversation(profile["id"], target_language="Spanish")

    app = FastAPI()
    app.include_router(live_session.router)
    client = TestClient(app)

    with client.websocket_connect("/ws/session") as ws:
        ws.send_json(
            {
                "type": "init",
                "profile_id": profile["id"],
                "profile_name": profile["name"],
                "conversation_id": conv["id"],
            }
        )
        msg = ws.receive_json()
        assert msg["type"] == "error"
        assert "API key" in msg["message"]
