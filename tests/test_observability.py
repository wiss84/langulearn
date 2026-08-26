"""Tests for observability.py's Langfuse tracing wrapper - specifically its
defensive behavior, since this wraps the live tutoring session's core loop
and must never let a tracing-side problem affect application behavior.
Doesn't require the `langfuse` package to be installed (it's an optional
extra, see pyproject.toml) - every test that needs a "working" client uses
a small fake instead of the real SDK.

conftest.py's isolated_observability fixture (autouse) already strips any
real LANGFUSE_* env vars and clears profile keys before every test - tests
here that want tracing enabled call observability.set_profile_keys()
explicitly instead of relying on real env vars ever being present during
a test run.
"""

import pytest

from thirtytutors import observability


class _FakeObservation:
    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _FakeSpanContext:
    """Stands in for what client.start_as_current_observation(...) returns -
    a context manager whose __enter__ yields an observation object."""

    def __init__(self, obs=None, fail_on_enter=False, fail_on_exit=False):
        self._obs = obs or _FakeObservation()
        self._fail_on_enter = fail_on_enter
        self._fail_on_exit = fail_on_exit

    def __enter__(self):
        if self._fail_on_enter:
            raise RuntimeError("boom on enter")
        return self._obs

    def __exit__(self, exc_type, exc, tb):
        if self._fail_on_exit and exc_type is None:
            raise RuntimeError("boom on exit")
        return False  # never swallow an exception from the caller's block


class _FakeClient:
    def __init__(self, span_context=None):
        self._span_context = span_context or _FakeSpanContext()
        self.calls = []

    def start_as_current_observation(self, **kwargs):
        self.calls.append(kwargs)
        return self._span_context


# --- is_enabled() reflects current keys ---


def test_is_enabled_false_without_keys():
    observability.set_profile_keys(None, None)
    assert observability.is_enabled() is False


def test_is_enabled_true_with_both_keys():
    observability.set_profile_keys("pk-test", "sk-test")
    assert observability.is_enabled() is True


# --- span(): disabled/no-op behavior (conftest already leaves ENABLED=False) ---


def test_span_noop_when_disabled():
    entered = False
    with observability.span("test") as obs:
        entered = True
        assert obs is None
    assert entered  # the caller's block still runs normally


# --- span(): normal operation ---


def test_span_yields_the_observation_object(monkeypatch):
    observability.set_profile_keys("pk-test", "sk-test")
    fake_obs = _FakeObservation()
    fake_client = _FakeClient(_FakeSpanContext(obs=fake_obs))
    monkeypatch.setattr(observability, "_get_client", lambda: fake_client)

    with observability.span("test") as obs:
        assert obs is fake_obs


def test_span_passes_input_metadata_model_through(monkeypatch):
    observability.set_profile_keys("pk-test", "sk-test")
    fake_client = _FakeClient()
    monkeypatch.setattr(observability, "_get_client", lambda: fake_client)

    with observability.span("test", input={"a": 1}, metadata={"b": 2}, model="gemini-x", as_type="generation"):
        pass

    assert fake_client.calls == [
        {"as_type": "generation", "name": "test", "input": {"a": 1}, "metadata": {"b": 2}, "model": "gemini-x"}
    ]


def test_span_omits_none_kwargs(monkeypatch):
    observability.set_profile_keys("pk-test", "sk-test")
    fake_client = _FakeClient()
    monkeypatch.setattr(observability, "_get_client", lambda: fake_client)

    with observability.span("test"):
        pass

    assert fake_client.calls == [{"as_type": "span", "name": "test"}]


# --- span(): the critical correctness property - caller exceptions always propagate ---


def test_span_propagates_caller_exceptions(monkeypatch):
    observability.set_profile_keys("pk-test", "sk-test")
    fake_client = _FakeClient()
    monkeypatch.setattr(observability, "_get_client", lambda: fake_client)

    class MyError(Exception):
        pass

    with pytest.raises(MyError):
        with observability.span("test"):
            raise MyError("real application error")

    # A real exception from inside the block must never be mistaken for a
    # tracing-side failure.
    assert observability._broken is False


def test_span_propagates_caller_exceptions_when_disabled():
    class MyError(Exception):
        pass

    with pytest.raises(MyError):
        with observability.span("test"):
            raise MyError("real application error")


# --- span(): defensive failure handling ---


def test_span_disables_tracing_when_start_fails(monkeypatch):
    observability.set_profile_keys("pk-test", "sk-test")
    fake_client = _FakeClient(_FakeSpanContext(fail_on_enter=True))
    monkeypatch.setattr(observability, "_get_client", lambda: fake_client)

    with observability.span("test") as obs:
        assert obs is None  # degrades to a no-op rather than raising

    assert observability._broken is True


def test_span_disables_tracing_when_exit_fails_but_body_still_runs(monkeypatch):
    observability.set_profile_keys("pk-test", "sk-test")
    fake_client = _FakeClient(_FakeSpanContext(fail_on_exit=True))
    monkeypatch.setattr(observability, "_get_client", lambda: fake_client)

    entered = False
    with observability.span("test") as obs:
        entered = True
        assert obs is not None
    assert entered
    assert observability._broken is True


def test_get_client_short_circuits_once_broken(monkeypatch):
    observability.set_profile_keys("pk-test", "sk-test")
    monkeypatch.setattr(observability, "_broken", True)
    assert observability._get_client() is None


def test_span_handles_missing_langfuse_package_for_attribute_propagation(monkeypatch):
    """session_id/user_id tagging needs its own `from langfuse import
    propagate_attributes` - if that fails (package genuinely not
    installed, since it's an optional extra), the span must still work,
    just without the tagging."""
    observability.set_profile_keys("pk-test", "sk-test")
    fake_client = _FakeClient()
    monkeypatch.setattr(observability, "_get_client", lambda: fake_client)

    entered = False
    with observability.span("test", session_id="conv-1", user_id="profile-1"):
        entered = True
    assert entered


# --- update() ---


def test_update_is_a_noop_for_none():
    observability.update(None, output="anything")  # must not raise


def test_update_calls_observation_update():
    fake_obs = _FakeObservation()
    observability.update(fake_obs, output="hello")
    assert fake_obs.updates == [{"output": "hello"}]


def test_update_disables_tracing_on_failure():
    class BadObs:
        def update(self, **kwargs):
            raise RuntimeError("boom")

    observability.update(BadObs(), output="x")
    assert observability._broken is True
