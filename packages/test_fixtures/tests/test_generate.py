import wave
from pathlib import Path

import numpy as np
import pytest

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


def _click_peak(data, sr: int, t0: float, window_s: float = 0.03) -> float:
    """Max |amplitude| in a short window starting at t0 seconds -- a proxy
    for "how loud was the click placed at this grid position."""
    i0 = int(t0 * sr)
    i1 = int((t0 + window_s) * sr)
    return float(np.max(np.abs(data[i0:i1])))


@pytest.mark.parametrize("tempo_bpm", [90.0, 140.0])
def test_generate_metered_clicks_duration_simple_meter(tmp_path: Path, tempo_bpm: float):
    from test_fixtures.generate import generate_metered_clicks

    path = generate_metered_clicks("3/4", tempo_bpm=tempo_bpm, measures=4, path=tmp_path / "m34.wav")
    assert path.exists()

    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        frames = wf.getnframes()
    # 3/4: one click per quarter beat; measure = 3 * (60/tempo)s; 4 measures.
    seconds_per_quarter = 60.0 / tempo_bpm
    expected_s = 4 * 3 * seconds_per_quarter
    assert abs(frames / sr - expected_s) < 0.1


def test_generate_metered_clicks_duration_compound_6_8(tmp_path: Path):
    from test_fixtures.generate import generate_metered_clicks

    path = generate_metered_clicks("6/8", tempo_bpm=120.0, measures=4, path=tmp_path / "m68.wav")
    assert path.exists()

    with wave.open(str(path), "rb") as wf:
        sr = wf.getframerate()
        frames = wf.getnframes()
    # 6/8 at 120bpm (quarter = 0.5s): measure = 3 quarter beats = 1.5s; 4 measures = 6s
    assert abs(frames / sr - 6.0) < 0.1


def test_generate_metered_clicks_rejects_unsupported_meter(tmp_path: Path):
    from test_fixtures.generate import generate_metered_clicks

    with pytest.raises(ValueError):
        generate_metered_clicks("13/16", tempo_bpm=120.0, measures=2, path=tmp_path / "x.wav")


@pytest.mark.parametrize("meter,n_clicks_per_measure", [("2/4", 2), ("3/4", 3)])
def test_generate_metered_clicks_simple_meter_accent_pattern(
    tmp_path: Path, meter: str, n_clicks_per_measure: int
):
    """Downbeat (grid slot 0) is loud (amp 1.0); every other beat is soft
    (amp 0.4) -- one click per quarter-note beat for simple meters."""
    from scipy.io import wavfile

    from test_fixtures.generate import generate_metered_clicks

    tempo_bpm = 100.0
    seconds_per_quarter = 60.0 / tempo_bpm
    path = generate_metered_clicks(meter, tempo_bpm=tempo_bpm, measures=3, path=tmp_path / "m.wav")
    sr, data = wavfile.read(str(path))

    # second measure (index 1), so the very first sample isn't a boundary case
    measure_len_s = n_clicks_per_measure * seconds_per_quarter
    peaks = [
        _click_peak(data, sr, measure_len_s * 1 + i * seconds_per_quarter)
        for i in range(n_clicks_per_measure)
    ]
    downbeat, *rest = peaks
    assert all(downbeat > weak for weak in rest)
    # off-beats are all roughly equally soft
    assert max(rest) - min(rest) < 0.15 * max(rest)


def test_generate_metered_clicks_compound_6_8_accent_pattern(tmp_path: Path):
    """6/8: one click per eighth note; eighths 0 and 3 are loud (0 louder
    than 3: 1.0 vs 0.7), the rest are soft (0.4)."""
    from scipy.io import wavfile

    from test_fixtures.generate import generate_metered_clicks

    tempo_bpm = 120.0
    seconds_per_eighth = (60.0 / tempo_bpm) / 2.0
    path = generate_metered_clicks("6/8", tempo_bpm=tempo_bpm, measures=3, path=tmp_path / "m68acc.wav")
    sr, data = wavfile.read(str(path))

    measure_len_s = 6 * seconds_per_eighth
    peaks = [
        _click_peak(data, sr, measure_len_s * 1 + i * seconds_per_eighth) for i in range(6)
    ]
    downbeat, secondary = peaks[0], peaks[3]
    weak = [peaks[i] for i in (1, 2, 4, 5)]

    assert downbeat > secondary > max(weak)
    assert max(weak) - min(weak) < 0.15 * max(weak)


@pytest.mark.parametrize(
    ("meter", "secondary_indices", "weak_indices"),
    [
        ("9/8", (3, 6), (1, 2, 4, 5, 7, 8)),
        ("12/8", (3, 6, 9), (1, 2, 4, 5, 7, 8, 10, 11)),
    ],
)
def test_generate_metered_clicks_compound_secondary_accents_land_on_group_starts(
    tmp_path: Path, meter: str, secondary_indices: tuple[int, ...], weak_indices: tuple[int, ...]
):
    """9/8 and 12/8: one click per eighth note; every dotted-quarter group
    start after the downbeat (every 3rd eighth) is a secondary accent
    (0.7); grid_size // 2 would land mid-group for 9/8 (index 4) instead
    of on a group start -- this pins the corrected, generalized indices."""
    from scipy.io import wavfile

    from test_fixtures.generate import generate_metered_clicks

    tempo_bpm = 120.0
    seconds_per_eighth = (60.0 / tempo_bpm) / 2.0
    grid_size = int(meter.split("/")[0])
    path = generate_metered_clicks(meter, tempo_bpm=tempo_bpm, measures=3, path=tmp_path / "m_group.wav")
    sr, data = wavfile.read(str(path))

    measure_len_s = grid_size * seconds_per_eighth
    peaks = [
        _click_peak(data, sr, measure_len_s * 1 + i * seconds_per_eighth) for i in range(grid_size)
    ]
    downbeat = peaks[0]
    secondary = [peaks[i] for i in secondary_indices]
    weak = [peaks[i] for i in weak_indices]

    assert downbeat > max(secondary)
    for s in secondary:
        assert s > max(weak)
    assert max(weak) - min(weak) < 0.15 * max(weak)
    assert max(secondary) - min(secondary) < 0.15 * max(secondary)
