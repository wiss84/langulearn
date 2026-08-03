"""Unit tests for retry.py's error classification and retryDelay parsing -
pure logic, no I/O, no mocking needed.
"""

import time

import pytest

from langulearn import retry

pytestmark = pytest.mark.unit


# --- is_transient_error ---


class ServerError(Exception):
    """Stands in for google.genai's own ServerError class. Must be named
    exactly this - is_transient_error checks type(e).__name__ == "ServerError"
    by name specifically, so it doesn't need a hard import dependency on
    the real SDK's error module (which has moved before across versions).
    """


def test_server_error_is_always_transient():
    assert retry.is_transient_error(ServerError("anything")) is True


@pytest.mark.parametrize(
    "message",
    [
        "500 INTERNAL",
        "503 UNAVAILABLE",
        "429 RESOURCE_EXHAUSTED",
        "some text mentioning internal error",
    ],
)
def test_transient_keyword_markers(message):
    assert retry.is_transient_error(Exception(message)) is True


@pytest.mark.parametrize(
    "message",
    [
        "400 Bad Request",
        "401 Unauthorized",
        "404 model not found",
    ],
)
def test_non_transient_errors_are_not_retried(message):
    assert retry.is_transient_error(Exception(message)) is False


# --- is_network_error ---


def test_os_error_subclasses_are_network_errors():
    import socket

    assert retry.is_network_error(socket.gaierror("getaddrinfo failed")) is True
    assert retry.is_network_error(ConnectionError("connection refused")) is True
    assert retry.is_network_error(OSError("generic os error")) is True


def test_api_errors_are_not_network_errors():
    # A real 429/500 from the SDK is a ServerError/ClientError, not an
    # OSError subclass - is_network_error must not misclassify those, or a
    # genuine rate-limit would get relabeled as "check your internet".
    assert retry.is_network_error(ServerError("500 INTERNAL")) is False
    assert retry.is_network_error(Exception("429 RESOURCE_EXHAUSTED")) is False


# --- is_rate_limit_error ---


@pytest.mark.parametrize("message", ["429 Too Many Requests", "RESOURCE_EXHAUSTED: quota"])
def test_rate_limit_markers(message):
    assert retry.is_rate_limit_error(Exception(message)) is True


def test_non_rate_limit_error_is_not_flagged():
    assert retry.is_rate_limit_error(Exception("500 INTERNAL")) is False


# --- parse_retry_delay ---


def test_parse_retry_delay_extracts_seconds_plus_margin():
    text = 'error details {"retryDelay": "23s"}'
    assert retry.parse_retry_delay(text) == pytest.approx(24.0)


def test_parse_retry_delay_returns_none_when_absent():
    assert retry.parse_retry_delay("500 INTERNAL, no delay mentioned") is None


# --- call_with_retry ---


def test_call_with_retry_returns_on_first_success():
    calls = []

    def fn():
        calls.append(1)
        return "ok"

    assert retry.call_with_retry(fn, max_retries=3, base_backoff=0) == "ok"
    assert len(calls) == 1


def test_call_with_retry_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ServerError("500 INTERNAL")
        return "recovered"

    result = retry.call_with_retry(fn, max_retries=4, base_backoff=0, label="test")
    assert result == "recovered"
    assert attempts["n"] == 3


def test_call_with_retry_raises_immediately_on_non_transient():
    def fn():
        raise ValueError("400 Bad Request")

    with pytest.raises(ValueError):
        retry.call_with_retry(fn, max_retries=4, base_backoff=0)


def test_call_with_retry_raises_last_error_once_exhausted(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda _: None)

    def fn():
        raise ServerError("503 UNAVAILABLE")

    with pytest.raises(ServerError):
        retry.call_with_retry(fn, max_retries=2, base_backoff=0)
