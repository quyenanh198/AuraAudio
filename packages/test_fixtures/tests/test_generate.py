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


def test_write_guitar_pluck_with_silence_wav_has_a_true_silent_gap(tmp_path: Path):
    import numpy as np
    from scipy.io import wavfile

    from test_fixtures.generate import write_guitar_pluck_with_silence_wav

    out_path = tmp_path / "silence_gap.wav"
    write_guitar_pluck_with_silence_wav(
        out_path, pre_note_count=2, silence_s=4.0, post_note_count=2,
        note_len=0.5, sample_rate=44100,
    )

    sr, data = wavfile.read(str(out_path))
    assert sr == 44100
    # total duration: 1.0s notes + 4.0s silence + 1.0s notes = 6.0s
    assert abs(len(data) / sr - 6.0) < 0.01

    # the mid-clip silence region (roughly [1.0, 5.0)s) must be true zero
    # signal, not merely quiet — pick a safely-interior slice to avoid any
    # edge/decay-tail ambiguity right at the note/silence boundary.
    silence_region = data[int(1.5 * sr):int(4.5 * sr)]
    assert np.max(np.abs(silence_region)) == 0

    # both the pre- and post-silence regions have real signal.
    pre_region = data[: int(0.4 * sr)]
    post_region = data[int(5.1 * sr):int(5.9 * sr)]
    assert np.max(np.abs(pre_region)) > 1000
    assert np.max(np.abs(post_region)) > 1000


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
