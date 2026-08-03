"""Local, self-hosted speaker verification (Resemblyzer only - see
requirements.txt for why ECAPA-TDNN/SpeechBrain were dropped).

A profile enrolls its voice once, then completes a threshold-calibration
test - both on the hands-free-setup page (routes_pages.py's
/handsfree-setup), driven through enrollment.py and the
/api/profiles/{id}/voice-enrollment* + /api/profiles/{id}/voice-enrollment-test
endpoints in routes_api.py. At runtime, a short window of live mic audio
gets checked against that profile's enrolled reference embedding and its
own calibrated threshold (verifier.py) to decide whether the audio
plausibly came from the enrolled speaker - so background conversation from
someone else in the room can be filtered out before it ever reaches
Gemini, rather than relying on Gemini's own speech-vs-silence VAD, which
has no concept of speaker identity.
"""

from .enrollment import (
    delete_enrollment,
    enroll_profile,
    has_enrollment,
    load_reference,
)
from .verifier import get_status, pcm16_bytes_to_float32, score, verify, warm_up

__all__ = [
    "delete_enrollment",
    "enroll_profile",
    "get_status",
    "has_enrollment",
    "load_reference",
    "pcm16_bytes_to_float32",
    "score",
    "verify",
    "warm_up",
]
