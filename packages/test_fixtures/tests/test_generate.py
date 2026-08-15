import wave
from pathlib import Path

from test_fixtures.generate import write_guitar_pluck_wav


def test_write_guitar_pluck_wav_produces_correct_duration_and_rate(tmp_path: Path):
    out_path = tmp_path / "fixture.wav"
    write_guitar_pluck_wav(out_path, duration_s=2.0, sample_rate=44100)

    with wave.open(str(out_path), "rb") as wf:
        assert wf.getframerate() == 44100
        frames = wf.getnframes()
        assert abs(frames / 44100 - 2.0) < 0.01


def test_write_guitar_pluck_wav_is_not_silent(tmp_path: Path):
    import numpy as np
    from scipy.io import wavfile

    out_path = tmp_path / "fixture.wav"
    write_guitar_pluck_wav(out_path, duration_s=1.0, sample_rate=22050)
    _, data = wavfile.read(str(out_path))
    assert np.max(np.abs(data)) > 1000
