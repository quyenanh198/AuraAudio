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


def test_write_metronome_pulse_wav_has_correct_duration_and_click_count(tmp_path: Path):
    import numpy as np
    from scipy.io import wavfile

    from test_fixtures.generate import write_metronome_pulse_wav

    out_path = tmp_path / "pulse.wav"
    write_metronome_pulse_wav(out_path, bpm=120.0, meter="4/4", duration_s=8.0, sample_rate=22050)

    sr, data = wavfile.read(str(out_path))
    assert sr == 22050
    # 8s at 120 BPM = 16 beats; each click has a brief attack, so count local peaks
    # above a high threshold as a proxy for "how many clicks landed."
    threshold = 0.5 * np.max(np.abs(data))
    above = np.abs(data) > threshold
    # Count contiguous above-threshold runs (each click's peak sample cluster)
    edges = np.diff(above.astype(int))
    click_count = int(np.sum(edges == 1))
    assert 10 <= click_count <= 20  # ~16 expected, generous tolerance for peak-detection noise


def test_write_metronome_pulse_wav_rejects_unknown_meter(tmp_path: Path):
    import pytest

    from test_fixtures.generate import write_metronome_pulse_wav

    with pytest.raises(KeyError):
        write_metronome_pulse_wav(tmp_path / "bad.wav", bpm=120.0, meter="7/8")


def test_write_diatonic_melody_wav_produces_correct_duration(tmp_path: Path):
    from scipy.io import wavfile

    from test_fixtures.generate import write_diatonic_melody_wav

    out_path = tmp_path / "melody.wav"
    write_diatonic_melody_wav(out_path, key="D major", duration_s=4.0, sample_rate=22050)

    sr, data = wavfile.read(str(out_path))
    assert sr == 22050
    assert abs(len(data) / sr - 4.0) < 0.01


def test_write_diatonic_melody_wav_is_not_silent(tmp_path: Path):
    import numpy as np
    from scipy.io import wavfile

    from test_fixtures.generate import write_diatonic_melody_wav

    out_path = tmp_path / "melody.wav"
    write_diatonic_melody_wav(out_path, key="A minor", duration_s=4.0, sample_rate=22050)
    _, data = wavfile.read(str(out_path))
    assert np.max(np.abs(data)) > 1000
