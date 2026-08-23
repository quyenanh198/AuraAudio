from __future__ import annotations

import sys
import wave

import numpy as np
import pytest
from aura_worker import binaries, separation
from aura_worker.binaries import ResolvedBinary
from aura_worker.separation import (
    DemucsWeightsMissingError,
    _decode_first_audio_stream,
    _resolve_weights_dir,
    separate_guitar,
)
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


def test_decode_first_audio_stream_matches_demucs_audiofile_output(workdir):
    """Windows hidden-console audit regression: `_decode_first_audio_stream`
    replaced `demucs.audio.AudioFile(...).read(...)` as the decode this
    module uses (see `separation.py`'s module docstring "OWN DECODE"
    paragraph) specifically so the ffmpeg/ffprobe shellouts go through
    THIS app's own `creationflags`-safe subprocess calls instead of
    demucs's un-audited internal ones. This test proves that swap didn't
    change the decoded SAMPLES: runs both the new helper and demucs's own
    `AudioFile` against the identical real WAV fixture and asserts the
    resulting tensors are numerically indistinguishable (`torch.allclose`,
    not `torch.equal`, since two independent ffmpeg invocations writing
    then reading a raw f32le stream can differ in the last ULP or two of
    floating-point precision, though in practice this passes bit-identical
    on this suite's ffmpeg build too)."""
    import torch
    from demucs.audio import AudioFile

    sample_rate = 44100
    duration_s = 1.0
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
    source_path = workdir / "tone.wav"
    wavfile.write(str(source_path), sample_rate, signal)

    target_samplerate = 44100
    ours = _decode_first_audio_stream(source_path, target_samplerate)
    theirs = AudioFile(str(source_path)).read(streams=0, samplerate=target_samplerate, channels=None)

    assert ours.shape == theirs.shape
    assert torch.allclose(ours, theirs, atol=1e-6)


def test_decode_first_audio_stream_raises_clear_error_when_ffmpeg_unresolved(workdir, monkeypatch):
    monkeypatch.setattr(separation, "resolve_binary", lambda name: None if name == "ffmpeg" else ResolvedBinary(path="ffprobe", source=binaries.ON_PATH))

    source_path = workdir / "tone.wav"
    wavfile.write(str(source_path), 22050, np.zeros(22050, dtype="int16"))

    with pytest.raises(RuntimeError, match="ffmpeg"):
        _decode_first_audio_stream(source_path, 22050)


def test_decode_first_audio_stream_raises_clear_error_when_ffprobe_unresolved(workdir, monkeypatch):
    monkeypatch.setattr(separation, "resolve_binary", lambda name: None if name == "ffprobe" else ResolvedBinary(path="ffmpeg", source=binaries.ON_PATH))

    source_path = workdir / "tone.wav"
    wavfile.write(str(source_path), 22050, np.zeros(22050, dtype="int16"))

    with pytest.raises(RuntimeError, match="ffprobe"):
        _decode_first_audio_stream(source_path, 22050)


def test_decode_first_audio_stream_passes_windows_creationflags_on_win32(workdir, monkeypatch):
    """Windows hidden-console audit: BOTH subprocess calls this helper
    makes (the ffprobe stream-info probe and the ffmpeg raw-PCM decode)
    must splat `aura_worker.binaries.subprocess_flags()`. `subprocess.run`
    is fully faked (not just wrapped) so `sys.platform` can be forced to
    `"win32"` (via `aura_worker.binaries.sys`) without also handing a
    Windows-only `creationflags` kwarg to this suite's real POSIX
    `subprocess.Popen`, which would raise."""
    import json

    monkeypatch.setattr(binaries.sys, "platform", "win32")
    monkeypatch.setattr(
        separation,
        "resolve_binary",
        lambda name: ResolvedBinary(path=name, source=binaries.ON_PATH),
    )

    source_path = workdir / "tone.wav"
    wavfile.write(str(source_path), 22050, np.zeros(22050, dtype="int16"))

    captured_calls: list[dict] = []

    def _fake_run(cmd, **kwargs):
        captured_calls.append(kwargs)
        if cmd[0] == "ffprobe":

            class _FakeCompletedProcess:
                stdout = json.dumps(
                    {"streams": [{"codec_type": "audio", "channels": 1, "sample_rate": "22050"}]}
                ).encode("utf-8")

            return _FakeCompletedProcess()
        # ffmpeg call -- write a tiny real f32le file so np.fromfile succeeds.
        out_path = cmd[-1]
        np.zeros(4, dtype=np.float32).tofile(out_path)
        return None

    monkeypatch.setattr(separation.subprocess, "run", _fake_run)

    _decode_first_audio_stream(source_path, 22050)

    assert len(captured_calls) == 2
    assert all(kwargs.get("creationflags") == 0x0800_0000 for kwargs in captured_calls)
