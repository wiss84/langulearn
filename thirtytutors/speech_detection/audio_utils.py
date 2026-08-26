"""Shared audio-shaping helpers for speech_detection - kept separate from
verifier.py/enrollment.py since both need the same trimming logic.
"""

import numpy as np

_FRAME_SAMPLES = 320  # 20ms at 16kHz


def trim_silence(audio: np.ndarray, rms_threshold: float, padding_frames: int = 3) -> np.ndarray:
    """Crops audio down to the region between its first and last frame with
    real energy, with a little padding kept on each side. Both a fixed 2s
    hands-free window and a toggle-recorded enrollment clip routinely carry
    real silence at the edges (recording starts a beat before speech, or
    trails off after it) - feeding that padding into the speaker-embedding
    model dilutes the embedding with non-speech content, which is a large
    part of why same-speaker cosine-similarity scores were coming back much
    lower than expected. This only affects what gets embedded for the
    speaker-verification check; the raw window's own RMS (used for the
    separate silence/turn-boundary decision) is computed before this runs.

    Returns the original array unchanged if nothing exceeds the threshold
    (an all-silence window/clip) - callers should already be filtering
    those out before this is reached.
    """
    if len(audio) < _FRAME_SAMPLES:
        return audio

    n_frames = len(audio) // _FRAME_SAMPLES
    frames = audio[: n_frames * _FRAME_SAMPLES].reshape(n_frames, _FRAME_SAMPLES)
    frame_rms = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))

    active = np.where(frame_rms >= rms_threshold)[0]
    if len(active) == 0:
        return audio

    start_frame = max(0, active[0] - padding_frames)
    end_frame = min(n_frames, active[-1] + 1 + padding_frames)
    return audio[start_frame * _FRAME_SAMPLES : end_frame * _FRAME_SAMPLES]
