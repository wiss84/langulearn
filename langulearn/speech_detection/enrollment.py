"""Voice-enrollment storage: given a profile's recorded sample clips,
embeds each and averages them into one reference vector, then
saves/loads/deletes that vector - keyed by PROFILE and MIC.

Per-mic, not just per-profile: a reference embedding isn't purely a
function of the speaker's voice - it's also shaped by the recording chain
(mic frequency response, gain, noise floor, and for Bluetooth mics, the
codec's own compression artifacts). Comparing runtime audio from one mic
against a reference enrolled on a different mic measurably degrades
genuine-speech similarity scores. So each mic gets its own reference, the
same way each mic already gets its own silence/similarity calibration (see
constants.py's mic_calibrations).

Storing the precomputed averaged embedding (not the raw audio) means
runtime verification never needs to re-embed the enrollment samples on
every check - only the live audio window gets embedded per comparison.
"""

import hashlib
import shutil

import numpy as np

from ..constants import (
    HANDSFREE_SILENCE_RMS_THRESHOLD,
    SPEAKER_VERIFICATION_BACKEND,
    VOICE_ENROLLMENT_DIR,
)
from . import resemblyzer_backend as _backend_module
from .audio_utils import trim_silence


def _mic_dir_name(mic_key: str) -> str:
    """Mic labels are arbitrary, user/OS-controlled strings and not
    guaranteed filesystem-safe - hash into a fixed-width, safe folder name.
    The label itself doesn't need to be recoverable from the folder name;
    profile["mic_calibrations"] is already the source of truth for what a
    mic is actually called.
    """
    return hashlib.sha1(mic_key.encode("utf-8")).hexdigest()[:16]


def _profile_root(profile_id: str):
    return VOICE_ENROLLMENT_DIR / profile_id


def _mic_dir(profile_id: str, mic_key: str):
    return _profile_root(profile_id) / _mic_dir_name(mic_key)


def _reference_path(profile_id: str, mic_key: str):
    return _mic_dir(profile_id, mic_key) / "reference_embedding.npy"


def _reference_backend_path(profile_id: str, mic_key: str):
    return _mic_dir(profile_id, mic_key) / "reference_backend.txt"


def has_enrollment(profile_id: str, mic_key: str) -> bool:
    """True only if a reference exists for THIS mic AND it was produced by
    the current backend (see load_reference) - a reference from a
    different embedding model isn't just less accurate, it's a different
    vector space entirely.
    """
    return load_reference(profile_id, mic_key) is not None


def load_reference(profile_id: str, mic_key: str) -> np.ndarray | None:
    path = _reference_path(profile_id, mic_key)
    backend_path = _reference_backend_path(profile_id, mic_key)
    if not path.exists() or not backend_path.exists():
        return None
    saved_backend = backend_path.read_text(encoding="utf-8").strip()
    if saved_backend != SPEAKER_VERIFICATION_BACKEND:
        print(
            f"[speech_detection] profile {profile_id!r}/mic {mic_key!r} was "
            f"enrolled under backend {saved_backend!r}, but "
            f"{SPEAKER_VERIFICATION_BACKEND!r} is now active - treating as "
            "not enrolled until re-recorded."
        )
        return None
    return np.load(path)


def enroll_profile(profile_id: str, mic_key: str, samples: list[np.ndarray]) -> np.ndarray:
    """samples: float32 mono 16kHz clips (the enrollment sentences, as
    recorded on THIS mic). Embeds each individually and averages into one
    reference vector, persisted under VOICE_ENROLLMENT_DIR (see
    constants.py - an OS-managed per-user data directory, not a path inside
    the project tree) as
    <profile_id>/<hashed mic_key>/reference_embedding.npy.
    """
    if not samples:
        raise ValueError("At least one enrollment sample is required.")

    # Silence-trim before embedding - otherwise the reference embedding
    # itself gets diluted by whatever silence padding the toggle-recorded
    # clip has at its start/end, making every later comparison score lower
    # than it should be. Same trim used on the runtime side (see
    # verifier.py) so both sides of a comparison are shaped the same way.
    trimmed = [trim_silence(sample, HANDSFREE_SILENCE_RMS_THRESHOLD) for sample in samples]
    embeddings = [_backend_module.embed(sample) for sample in trimmed]
    reference = np.mean(np.stack(embeddings), axis=0)

    mic_dir = _mic_dir(profile_id, mic_key)
    mic_dir.mkdir(parents=True, exist_ok=True)
    np.save(_reference_path(profile_id, mic_key), reference)
    _reference_backend_path(profile_id, mic_key).write_text(SPEAKER_VERIFICATION_BACKEND, encoding="utf-8")
    return reference


def delete_enrollment(profile_id: str, mic_key: str | None = None) -> None:
    """mic_key=None deletes every mic's enrollment for this profile (used
    by routes_api.py's delete_profile, alongside the existing conversation
    cleanup); a specific mic_key deletes just that mic's reference (a fresh
    enroll_profile call for the same mic overwrites it naturally either
    way, so this is mainly for the whole-profile-deletion case).
    """
    if mic_key is None:
        root = _profile_root(profile_id)
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
    else:
        mic_dir = _mic_dir(profile_id, mic_key)
        if mic_dir.exists():
            shutil.rmtree(mic_dir, ignore_errors=True)
