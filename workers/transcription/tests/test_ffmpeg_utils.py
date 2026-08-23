import json

import pytest
from aura_worker import binaries
from aura_worker.binaries import ResolvedBinary
from aura_worker.errors import JobFailure
from aura_worker.ffmpeg_utils import probe_media, sha256_file
from test_fixtures.generate import write_guitar_pluck_wav


def test_probe_media_reads_duration_and_sample_rate(workdir):
    wav_path = workdir / "fixture.wav"
    write_guitar_pluck_wav(wav_path, duration_s=2.0, sample_rate=44100)

    info = probe_media(wav_path)

    assert info.container == "wav"
    assert info.sample_rate == 44100
    assert 1900 <= info.duration_ms <= 2100


def test_probe_media_rejects_nonexistent_file(workdir):
    with pytest.raises(JobFailure) as exc_info:
        probe_media(workdir / "missing.wav")
    assert exc_info.value.code.value == "DECODE_FAILED"


def test_sha256_file_is_deterministic(workdir):
    wav_path = workdir / "fixture.wav"
    write_guitar_pluck_wav(wav_path, duration_s=1.0, sample_rate=22050)
    assert sha256_file(wav_path) == sha256_file(wav_path)


def test_probe_media_raises_clear_error_when_ffprobe_unresolved(workdir, monkeypatch):
    # Root-cause regression: an unresolvable ffprobe (not on PATH, not at
    # any known install location) must fail with a clear, actionable
    # message -- not a raw FileNotFoundError from subprocess.run trying to
    # exec the literal string "ffprobe".
    import aura_worker.ffmpeg_utils as ffmpeg_utils_module

    wav_path = workdir / "fixture.wav"
    write_guitar_pluck_wav(wav_path, duration_s=1.0, sample_rate=22050)
    monkeypatch.setattr(ffmpeg_utils_module, "resolve_binary", lambda _name: None)

    with pytest.raises(JobFailure) as exc_info:
        probe_media(wav_path)
    assert exc_info.value.code.value == "DECODE_FAILED"
    assert "ffprobe" in exc_info.value.detail


def test_probe_media_passes_windows_creationflags_when_on_win32(workdir, monkeypatch):
    """Windows hidden-console audit: `probe_media`'s ffprobe spawn must
    splat `aura_worker.binaries.subprocess_flags()` into its
    `subprocess.run` call so a packaged Windows build never flashes a
    console window for it. `subprocess.run` itself is monkeypatched here
    (not run for real) so this test can force `sys.platform == "win32"`
    (via `aura_worker.binaries.sys`, which `subprocess_flags()` actually
    reads) and run identically on this suite's real Linux/macOS/CI host."""
    import aura_worker.ffmpeg_utils as ffmpeg_utils_module

    monkeypatch.setattr(binaries.sys, "platform", "win32")
    monkeypatch.setattr(
        ffmpeg_utils_module, "resolve_binary", lambda _name: ResolvedBinary(path="ffprobe", source=binaries.ON_PATH)
    )

    wav_path = workdir / "fixture.wav"
    write_guitar_pluck_wav(wav_path, duration_s=1.0, sample_rate=22050)

    captured: dict = {}

    class _FakeCompletedProcess:
        stdout = json.dumps(
            {
                "streams": [{"codec_type": "audio", "codec_name": "pcm_s16le", "sample_rate": "22050"}],
                "format": {"duration": "1.0"},
            }
        )

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        return _FakeCompletedProcess()

    monkeypatch.setattr(ffmpeg_utils_module.subprocess, "run", _fake_run)

    probe_media(wav_path)

    assert captured.get("creationflags") == 0x0800_0000
