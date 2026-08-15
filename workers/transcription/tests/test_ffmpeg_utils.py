import pytest

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
