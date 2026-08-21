from __future__ import annotations

import sys
import wave

import numpy as np
import pytest
from aura_worker import separation
from aura_worker.separation import DemucsWeightsMissingError, _resolve_weights_dir, separate_guitar
from scipy.io import wavfile


def test_resolve_weights_dir_env_override_wins(monkeypatch, tmp_path):
    fake = tmp_path / "custom_weights"
    monkeypatch.setenv("AURA_DEMUCS_WEIGHTS_DIR", str(fake))
    assert _resolve_weights_dir() == fake


def test_resolve_weights_dir_frozen_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.delenv("AURA_DEMUCS_WEIGHTS_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resolved = _resolve_weights_dir()
    assert resolved == tmp_path / "demucs_weights"


def test_resolve_weights_dir_dev_mode_is_repo_relative(monkeypatch):
    monkeypatch.delenv("AURA_DEMUCS_WEIGHTS_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    resolved = _resolve_weights_dir()
    assert resolved.parts[-2:] == ("weights", "demucs")


def test_separate_guitar_raises_when_weights_missing(monkeypatch, tmp_path, workdir):
    monkeypatch.setenv("AURA_DEMUCS_WEIGHTS_DIR", str(tmp_path / "does-not-exist"))
    monkeypatch.setattr(separation, "_model", None)  # reset the lazy singleton between tests

    wav_path = workdir / "silence.wav"
    wavfile.write(str(wav_path), 22050, np.zeros(22050, dtype="int16"))

    with pytest.raises(DemucsWeightsMissingError):
        separate_guitar(wav_path, workdir / "out.wav")


def test_separate_guitar_produces_valid_wav_from_real_weights(monkeypatch, workdir):
    """Real end-to-end run against the real, build-time-fetched
    htdemucs_6s weights (scripts/fetch_demucs_weights.py must have been
    run first -- same requirement as the piano checkpoint's real tests).
    Uses a short (2s) clip to keep this fast; demucs's fixed per-call
    overhead dominates short clips (see docs/benchmarks/2026-08-21-dq3.md's
    CPU-timing section), so this does not meaningfully undercount cost."""
    monkeypatch.delenv("AURA_DEMUCS_WEIGHTS_DIR", raising=False)
    monkeypatch.setattr(separation, "_model", None)

    sample_rate = 22050
    duration_s = 2.0
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
    source_path = workdir / "tone.wav"
    wavfile.write(str(source_path), sample_rate, signal)

    out_path = workdir / "separated.wav"
    result_path = separate_guitar(source_path, out_path)

    assert result_path == out_path
    assert out_path.exists()
    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == separation._load_model().samplerate
        # Duration should be roughly the input duration (demucs pads/trims
        # to its own segment boundaries internally, so allow real slack).
        actual_duration_s = wav_file.getnframes() / wav_file.getframerate()
        assert actual_duration_s == pytest.approx(duration_s, abs=1.0)


def test_separate_guitar_is_deterministic_across_calls(monkeypatch, workdir):
    """Pins the fix for a real code-review finding: apply_model's own
    `shifts` parameter defaults to 1 (a random time-shift augmentation,
    not just floating-point noise), which made two back-to-back calls on
    identical input produce meaningfully different output -- see
    separate_guitar's DETERMINISM docstring paragraph and
    docs/benchmarks/2026-08-21-dq3.md's "Determinism" section. Two
    separate calls (not just two reads of one cached output) must produce
    bit-for-bit identical bytes."""
    monkeypatch.delenv("AURA_DEMUCS_WEIGHTS_DIR", raising=False)
    monkeypatch.setattr(separation, "_model", None)

    sample_rate = 22050
    duration_s = 1.5
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = (0.3 * np.sin(2 * np.pi * 220 * t) + 0.15 * np.sin(2 * np.pi * 440 * t)) * 32767
    source_path = workdir / "tone.wav"
    wavfile.write(str(source_path), sample_rate, signal.astype(np.int16))

    out1 = separate_guitar(source_path, workdir / "sep1.wav")
    out2 = separate_guitar(source_path, workdir / "sep2.wav")

    assert out1.read_bytes() == out2.read_bytes()


def test_separate_guitar_handles_silent_input_without_crashing(monkeypatch, workdir):
    """Degenerate all-zero input would divide by zero in the
    normalize-by-std step without the explicit guard -- exercises that
    guard against the real weights."""
    monkeypatch.delenv("AURA_DEMUCS_WEIGHTS_DIR", raising=False)
    monkeypatch.setattr(separation, "_model", None)

    source_path = workdir / "silence.wav"
    wavfile.write(str(source_path), 22050, np.zeros(int(22050 * 1.0), dtype="int16"))

    out_path = workdir / "separated.wav"
    result_path = separate_guitar(source_path, out_path)

    assert result_path.exists()
