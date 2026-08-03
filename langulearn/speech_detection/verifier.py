"""Runtime speaker-verification gate: given a profile's enrolled reference
embedding and a short window of live mic audio, decides whether that audio
plausibly came from the enrolled speaker. Resemblyzer only - ECAPA-TDNN was
evaluated and dropped (see requirements.txt for why).

This module doesn't do any windowing/buffering itself - it's a pure
check-this-audio-window function. Whatever accumulates the rolling window
of raw mic audio (independent of Gemini's own turn-boundary signals, since
the decision to forward audio to Gemini has to happen before Gemini has
seen it) is the caller's responsibility.
"""

import numpy as np

from ..constants import HANDSFREE_SILENCE_RMS_THRESHOLD, SPEAKER_VERIFICATION_THRESHOLD
from . import resemblyzer_backend as _backend_module
from .audio_utils import trim_silence
from .enrollment import load_reference

# "not_started" | "loading" | "ready" | "failed" - lets the frontend (see
# get_speech_detection_status in routes_api.py) tell a user "the model is
# still downloading" instead of a Record/threshold-test click silently
# hanging or erroring the first time the app is ever run on a machine.
# Written from warm_up() at app startup; read from
# the /api/speech-detection-status endpoint.
_status = "not_started"


def get_status() -> str:
    return _status


def warm_up() -> None:
    """Loads the model into memory ahead of first use. Safe to call
    repeatedly (get_model() is itself a lazy singleton, so a second call is
    just a cheap no-op check) and safe to call from a background thread -
    failures are logged, never raised, since this is purely an optimization
    and should never block or break session start.
    """
    global _status
    _status = "loading"
    try:
        _backend_module.get_model()
        _status = "ready"
    except (ImportError, RuntimeError, OSError) as e:
        _status = "failed"
        print(f"[speech_detection] warm_up failed: {type(e).__name__}: {e}")


def pcm16_bytes_to_float32(pcm_bytes: bytes) -> np.ndarray:
    """Converts raw little-endian int16 PCM (this app's existing mic-audio
    wire format - see audio_chunk handling in live_session.py) into the
    float32 [-1, 1] range the backend expects.
    """
    int16 = np.frombuffer(pcm_bytes, dtype=np.int16)
    return int16.astype(np.float32) / 32768.0


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    if a.shape != b.shape:
        # Shouldn't happen in normal operation, but stays as a clear,
        # actionable failure rather than a raw numpy ValueError if it ever
        # does (e.g. manually edited/corrupted embedding data).
        raise ValueError(f"Embedding shape mismatch ({a.shape} vs {b.shape}) - re-record voice enrollment for this profile.")
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


def score(
    profile_id: str,
    mic_key: str,
    audio: np.ndarray,
    silence_rms_threshold: float | None = None,
) -> float | None:
    """Raw cosine-similarity score between this audio and THIS MIC's
    enrolled reference for this profile - no threshold applied. Returns
    None if this profile/mic combination has no enrollment on file. This is
    what the threshold-calibration test page (see routes_api.py's
    voice-enrollment-test endpoint) uses directly; verify() below is a
    thin threshold-comparison wrapper around this for the hands-free
    runtime path.
    """
    reference = load_reference(profile_id, mic_key)
    if reference is None:
        return None
    trimmed = trim_silence(audio, silence_rms_threshold or HANDSFREE_SILENCE_RMS_THRESHOLD)
    embedding = _backend_module.embed(trimmed)
    return cosine_similarity(embedding, reference)


def verify(
    profile_id: str,
    mic_key: str,
    audio: np.ndarray,
    silence_rms_threshold: float | None = None,
    similarity_threshold: float | None = None,
) -> tuple[bool, float] | None:
    """audio: float32 mono samples at 16kHz (a rolling ~2s window of live
    mic audio). Returns (passed, similarity_score), or None if this
    profile has no voice enrollment on file for THIS mic - callers should
    treat that as "gate inapplicable," not as a failed check, and let audio
    through unfiltered until the profile enrolls on this mic.

    mic_key: identifies which mic's reference/calibration to use (see
    constants.py's mic_calibrations docstring) - a reference enrolled on a
    different mic is not just less accurate but measurably wrong to compare
    against, since the recording chain (frequency response, gain, codec
    artifacts for Bluetooth) shapes the embedding as much as the voice does.

    silence_rms_threshold: the caller's own silence/turn-boundary threshold
    (falls back to the global default) - used here to trim silence padding
    out of the window before embedding it.

    similarity_threshold: the profile's own calibrated threshold for this
    mic, from the hands-free-setup page's threshold test (falls back to
    the global default, which is only really meant for that test page
    itself - see constants.SPEAKER_VERIFICATION_THRESHOLD).
    """
    s = score(profile_id, mic_key, audio, silence_rms_threshold)
    if s is None:
        return None
    threshold = similarity_threshold if similarity_threshold is not None else SPEAKER_VERIFICATION_THRESHOLD
    return s >= threshold, s
