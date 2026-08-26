"""Resemblyzer speaker-embedding backend - the lighter-weight fallback
candidate to ecapa.py. Same lazy-singleton shape and same embed(audio)
interface, so verifier.py and enrollment.py can treat both backends
identically and switching between them is a one-line constants change.
"""

import numpy as np

_encoder = None


def get_model():
    global _encoder
    if _encoder is None:
        from resemblyzer import VoiceEncoder

        _encoder = VoiceEncoder()
    return _encoder


def embed(audio: np.ndarray) -> np.ndarray:
    """audio: float32 mono samples at 16kHz, range [-1, 1]."""
    encoder = get_model()
    return encoder.embed_utterance(np.asarray(audio, dtype=np.float32))
