"""Tests for speech_detection's pure-math and storage logic (silence
trimming, cosine similarity, enroll/verify round-trip) using the
fake_speaker_backend fixture from conftest.py in place of the real
resemblyzer model - see that fixture's docstring for why that's a safe
substitution here.
"""

import numpy as np
import pytest

from thirtytutors.speech_detection import audio_utils, enrollment, verifier

# --- trim_silence ---

pytestmark_unit = pytest.mark.unit


def _tone(n_samples: int, amplitude: float) -> np.ndarray:
    return np.full(n_samples, amplitude, dtype=np.float32)


@pytest.mark.unit
def test_trim_silence_returns_short_arrays_unchanged():
    audio = np.ones(100, dtype=np.float32)  # shorter than one 320-sample frame
    assert np.array_equal(audio_utils.trim_silence(audio, rms_threshold=0.01), audio)


@pytest.mark.unit
def test_trim_silence_returns_all_silence_unchanged():
    audio = np.zeros(320 * 10, dtype=np.float32)
    assert np.array_equal(audio_utils.trim_silence(audio, rms_threshold=0.01), audio)


@pytest.mark.unit
def test_trim_silence_crops_around_active_region_with_padding():
    frame = 320
    silence = np.zeros(frame, dtype=np.float32)
    loud = _tone(frame, 1.0)
    # 5 silent frames, 2 loud frames, 5 silent frames
    audio = np.concatenate([silence] * 5 + [loud] * 2 + [silence] * 5)

    trimmed = audio_utils.trim_silence(audio, rms_threshold=0.5, padding_frames=1)

    # Active frames are index 5-6; with 1 frame of padding, expect frames 4-7 to survive.
    assert len(trimmed) == frame * 4
    assert trimmed.min() == pytest.approx(0.0)  # padding frames are still silent
    assert trimmed.max() == pytest.approx(1.0)  # loud frames survived


# --- cosine_similarity ---


@pytest.mark.unit
def test_cosine_similarity_identical_vectors_is_one():
    v = np.array([1.0, 2.0, 3.0], dtype=np.float32)
    assert verifier.cosine_similarity(v, v.copy()) == pytest.approx(1.0)


@pytest.mark.unit
def test_cosine_similarity_orthogonal_vectors_is_zero():
    a = np.array([1.0, 0.0], dtype=np.float32)
    b = np.array([0.0, 1.0], dtype=np.float32)
    assert verifier.cosine_similarity(a, b) == pytest.approx(0.0)


@pytest.mark.unit
def test_cosine_similarity_zero_vector_is_zero_not_nan():
    a = np.zeros(4, dtype=np.float32)
    b = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
    assert verifier.cosine_similarity(a, b) == 0.0


@pytest.mark.unit
def test_cosine_similarity_shape_mismatch_raises():
    a = np.zeros(4, dtype=np.float32)
    b = np.zeros(8, dtype=np.float32)
    with pytest.raises(ValueError):
        verifier.cosine_similarity(a, b)


# --- pcm16_bytes_to_float32 ---


@pytest.mark.unit
def test_pcm16_bytes_to_float32_round_trip():
    import struct

    samples = [0, 16384, -16384, 32767, -32768]
    pcm_bytes = struct.pack(f"<{len(samples)}h", *samples)
    floats = verifier.pcm16_bytes_to_float32(pcm_bytes)

    assert floats[0] == pytest.approx(0.0)
    assert floats[1] == pytest.approx(0.5, abs=1e-4)
    assert floats[2] == pytest.approx(-0.5, abs=1e-4)
    assert floats[4] == pytest.approx(-1.0, abs=1e-4)


# --- enrollment + verifier round trip (integration - uses the isolated
# data dir + fake speaker backend together) ---


@pytest.mark.integration
def test_has_enrollment_false_before_enrolling(fake_speaker_backend):
    assert enrollment.has_enrollment("profile-1", "mic-a") is False


@pytest.mark.integration
def test_enroll_then_verify_same_audio_scores_high(fake_speaker_backend):
    samples = [np.random.RandomState(0).uniform(-1, 1, 320 * 20).astype(np.float32)]
    enrollment.enroll_profile("profile-1", "mic-a", samples)

    assert enrollment.has_enrollment("profile-1", "mic-a") is True

    # Same content, silence-trimmed the same way on both sides -> identical
    # fake embedding -> cosine similarity of exactly 1.0.
    result = verifier.verify("profile-1", "mic-a", samples[0], similarity_threshold=0.9)
    assert result is not None
    passed, score = result
    assert passed is True
    assert score == pytest.approx(1.0)


@pytest.mark.integration
def test_verify_returns_none_without_enrollment(fake_speaker_backend):
    audio = np.random.RandomState(1).uniform(-1, 1, 320 * 20).astype(np.float32)
    assert verifier.verify("no-such-profile", "mic-a", audio) is None
    assert verifier.score("no-such-profile", "mic-a", audio) is None


@pytest.mark.integration
def test_verify_fails_below_threshold(fake_speaker_backend):
    enroll_sample = np.random.RandomState(0).uniform(-1, 1, 320 * 20).astype(np.float32)
    enrollment.enroll_profile("profile-1", "mic-a", [enroll_sample])

    # A very different signal (different mean/std/max/len -> different fake
    # embedding) should score low against the reference.
    different_audio = np.full(320 * 5, 0.01, dtype=np.float32)
    passed, _score = verifier.verify("profile-1", "mic-a", different_audio, similarity_threshold=0.999)
    assert passed is False


@pytest.mark.integration
def test_enrollment_is_scoped_per_mic(fake_speaker_backend):
    sample = np.random.RandomState(0).uniform(-1, 1, 320 * 20).astype(np.float32)
    enrollment.enroll_profile("profile-1", "mic-a", [sample])

    assert enrollment.has_enrollment("profile-1", "mic-a") is True
    assert enrollment.has_enrollment("profile-1", "mic-b") is False  # different mic, no enrollment


@pytest.mark.integration
def test_load_reference_rejects_mismatched_backend_tag(fake_speaker_backend):
    sample = np.random.RandomState(0).uniform(-1, 1, 320 * 20).astype(np.float32)
    enrollment.enroll_profile("profile-1", "mic-a", [sample])
    assert enrollment.has_enrollment("profile-1", "mic-a") is True

    # Simulate a reference saved by a since-retired backend (this bit the
    # app for real once already switching from ECAPA-TDNN to Resemblyzer).
    backend_path = enrollment._reference_backend_path("profile-1", "mic-a")
    backend_path.write_text("some-old-retired-backend", encoding="utf-8")

    assert enrollment.load_reference("profile-1", "mic-a") is None
    assert enrollment.has_enrollment("profile-1", "mic-a") is False


@pytest.mark.integration
def test_delete_enrollment_specific_mic_leaves_others_intact(fake_speaker_backend):
    sample = np.random.RandomState(0).uniform(-1, 1, 320 * 20).astype(np.float32)
    enrollment.enroll_profile("profile-1", "mic-a", [sample])
    enrollment.enroll_profile("profile-1", "mic-b", [sample])

    enrollment.delete_enrollment("profile-1", "mic-a")

    assert enrollment.has_enrollment("profile-1", "mic-a") is False
    assert enrollment.has_enrollment("profile-1", "mic-b") is True


@pytest.mark.integration
def test_delete_enrollment_all_mics_when_mic_key_omitted(fake_speaker_backend):
    sample = np.random.RandomState(0).uniform(-1, 1, 320 * 20).astype(np.float32)
    enrollment.enroll_profile("profile-1", "mic-a", [sample])
    enrollment.enroll_profile("profile-1", "mic-b", [sample])

    enrollment.delete_enrollment("profile-1")

    assert enrollment.has_enrollment("profile-1", "mic-a") is False
    assert enrollment.has_enrollment("profile-1", "mic-b") is False


@pytest.mark.integration
def test_enroll_profile_requires_at_least_one_sample(fake_speaker_backend):
    with pytest.raises(ValueError):
        enrollment.enroll_profile("profile-1", "mic-a", [])
