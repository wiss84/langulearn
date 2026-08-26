"""Optional Langfuse tracing (langfuse.com) for the live tutoring session.

Fully opt-in: set LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, and
LANGFUSE_BASE_URL as environment variables to enable it - these are the
exact environment variable names the Langfuse Python SDK itself reads
automatically to configure a client with no arguments, nothing specific to
this app. Without them set, every function here is a no-op, so running
this app never requires a Langfuse account or the `langfuse` package to be
installed at all.

Per-profile keys are also supported: if a profile supplies its own
langfuse_public_key / langfuse_secret_key / langfuse_base_url, those
override the environment variables for that session only.

Every call into the Langfuse SDK is isolated: a tracing-side failure (an
SDK version mismatch, a network error reaching Langfuse, an auth problem)
prints a warning and disables tracing for the rest of the process, rather
than ever interrupting a real tutoring session over it. A real exception
raised by the code *inside* a traced block is a completely different case
and always propagates normally - tracing problems and application
problems are never allowed to look the same to a caller.
"""

import os
import sys
from contextlib import contextmanager

from dotenv import load_dotenv

load_dotenv()

_profile_keys = {
    "public_key": os.environ.get("LANGFUSE_PUBLIC_KEY"),
    "secret_key": os.environ.get("LANGFUSE_SECRET_KEY"),
    "base_url": os.environ.get("LANGFUSE_BASE_URL"),
}

_client = None
_broken = False  # set once tracing has failed - stops retrying for the rest of the process rather than failing repeatedly


def _mark_broken(context: str, e: Exception) -> None:
    global _broken
    print(f"[observability] Langfuse {context} - disabling tracing for the rest of this run: {type(e).__name__}: {e}")
    _broken = True


def is_enabled() -> bool:
    return bool(_profile_keys["public_key"]) and bool(_profile_keys["secret_key"])


def set_profile_keys(
    public_key: str | None,
    secret_key: str | None,
    base_url: str | None = None,
) -> None:
    global _client
    _client = None
    _profile_keys["public_key"] = public_key or None
    _profile_keys["secret_key"] = secret_key or None
    _profile_keys["base_url"] = base_url or None


def _get_client():
    global _client
    if not is_enabled() or _broken:
        return None
    if _client is None:
        try:
            from langfuse import Langfuse

            kwargs = {
                "public_key": _profile_keys["public_key"],
                "secret_key": _profile_keys["secret_key"],
            }
            if _profile_keys.get("base_url"):
                kwargs["host"] = _profile_keys["base_url"]
            _client = Langfuse(**kwargs)
        except Exception as e:  # noqa: BLE001
            _mark_broken("client initialization failed", e)
            return None
    return _client


@contextmanager
def span(
    name: str,
    input=None,
    metadata: dict | None = None,
    as_type: str = "span",
    model: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
):
    """One traced observation - a tool call, a conversation turn, a quiz
    event. Yields the Langfuse observation object (call .update(output=...)
    on it once a result is known), or None if tracing is disabled/broken -
    callers should treat None as "do nothing further", same as every other
    optional-feature guard in this codebase.

    session_id/user_id (when given) tag this observation so Langfuse's UI
    groups it with every other observation from the same conversation -
    there's no single long-lived "session trace" wrapping the whole
    websocket connection, since retrofitting that around ws_session's
    existing control flow (multiple early returns, a mid-session
    reconnect-and-continue path) would be far riskier than tagging each
    observation independently.
    """
    client = _get_client()
    if client is None:
        yield None
        return

    attrs_cm = None
    if session_id or user_id:
        try:
            from langfuse import propagate_attributes

            attrs_cm = propagate_attributes(session_id=session_id, user_id=user_id)
            attrs_cm.__enter__()
        except Exception as e:  # noqa: BLE001
            _mark_broken(f"span '{name}' attribute propagation failed to start", e)
            attrs_cm = None  # continue without session/user tagging rather than aborting the whole span over it

    kwargs = {"as_type": as_type, "name": name}
    if input is not None:
        kwargs["input"] = input
    if metadata is not None:
        kwargs["metadata"] = metadata
    if model is not None:
        kwargs["model"] = model

    try:
        cm = client.start_as_current_observation(**kwargs)
        obs = cm.__enter__()
    except Exception as e:  # noqa: BLE001
        _mark_broken(f"span '{name}' failed to start", e)
        if attrs_cm is not None:
            try:
                attrs_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        yield None
        return

    try:
        yield obs
    except BaseException:
        # A real exception from the caller's block - let Langfuse record it
        # on the observation if it can, but the exception itself must
        # always propagate unchanged; a tracing-side problem here must
        # never end up masking or replacing it.
        try:
            cm.__exit__(*sys.exc_info())
        except Exception:  # noqa: BLE001
            pass
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception as e:  # noqa: BLE001
            _mark_broken(f"span '{name}' failed to close", e)
    finally:
        if attrs_cm is not None:
            try:
                attrs_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass


def update(obs, **kwargs) -> None:
    """Best-effort obs.update(...) - obs is None whenever tracing is
    disabled/broken, so every call site would otherwise need its own
    `if obs is not None` guard around a raw .update() call."""
    if obs is None:
        return
    try:
        obs.update(**kwargs)
    except Exception as e:  # noqa: BLE001
        _mark_broken("observation update failed", e)
