"""
Retry helper for Gemini API calls (Live API connect + summarization).

AI Studio's free tier throws a fair number of transient errors - 500
INTERNAL, 503 UNAVAILABLE, and occasionally 429 RESOURCE_EXHAUSTED even
under modest use - that succeed if you just try again after a short wait.
This mirrors the retry half of Local Search Agent's
agent/rate_limit_handler.py (same transient-error classification,
retryDelay parsing, exponential backoff), but deliberately leaves out that
module's quota tracking / shared-instance registry / concurrency gate:
those exist there because many workspaces can share one account's real
RPM/TPM/RPD budget concurrently. This is a single-user local app with
exactly one Live session and one summarization call ever in flight at a
time, so none of that machinery earns its complexity here - just retry.
"""

import logging
import re
import time
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 4  # additional attempts after the first, so 5 tries total
DEFAULT_BASE_BACKOFF = 2.0  # seconds, doubles each attempt


def is_transient_error(e: Exception) -> bool:
    """Best-effort classification of 'worth retrying' vs 'will never work'.

    The google-genai SDK raises ServerError for 5xx responses and
    ClientError for 4xx (429 included) - checking the class name avoids a
    hard import dependency on the exact error module path, which has moved
    before across SDK versions. ServerError is unconditionally transient;
    everything else (ClientError, or any other exception type, e.g. a
    network-level error raised before a response is even parsed) falls
    through to a keyword scan of the message for the usual transient
    markers - this is what catches a 429 ClientError too, without treating
    every ClientError (400 bad request, 401 auth, 404 unknown model) as
    retryable.
    """
    if type(e).__name__ == "ServerError":
        return True
    text = str(e)
    return any(marker in text for marker in ("INTERNAL", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "429", " 500", " 503"))


def parse_retry_delay(text: str) -> float | None:
    """Extracts Google's own suggested retryDelay (e.g. 'retryDelay":
    "23s"') from a 429 error message, if present - more accurate than a
    generic backoff guess when the server states exactly how long to wait.
    A flat 1s is added as a small safety margin."""
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
    return float(match.group(1)) + 1.0 if match else None


def call_with_retry(
    fn: Callable[..., T],
    *args,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_backoff: float = DEFAULT_BASE_BACKOFF,
    label: str = "call",
    **kwargs,
) -> T:
    """Calls fn(*args, **kwargs), retrying on transient errors with
    exponential backoff (or Google's own suggested retryDelay when a 429
    response includes one). Re-raises immediately on a non-transient
    error, and re-raises the last error once max_retries is exhausted -
    the caller decides what "give up" means (main.py's summarize_conversation
    wraps this in its own try/except and just logs, since a skipped
    summary fold isn't fatal and will be retried next time one is due).

    Synchronous by design - built for the sync google-genai text-generation
    call used by summarization, which itself runs inside asyncio.to_thread
    so a blocking time.sleep() here doesn't stall the event loop. The Live
    API connection has its own async retry loop instead
    (main.py's _connect_live_with_retries) since it manages an async
    context manager rather than a plain function call - but both share
    this module's is_transient_error/parse_retry_delay for classification.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_exc = e
            if not is_transient_error(e) or attempt == max_retries:
                raise
            delay = parse_retry_delay(str(e)) or (base_backoff * (2 ** attempt))
            logger.warning(
                "[%s] attempt %d/%d failed (%s) - retrying in %.1fs",
                label, attempt + 1, max_retries + 1, type(e).__name__, delay,
            )
            time.sleep(delay)
    raise last_exc  # unreachable, keeps type checkers happy
