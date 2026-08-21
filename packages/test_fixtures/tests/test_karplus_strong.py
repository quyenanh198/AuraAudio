import numpy as np
from test_fixtures.generate import karplus_strong_pluck, scale_pitches


def test_karplus_strong_pluck_produces_correct_sample_count():
    signal = karplus_strong_pluck(freq=110.0, duration_s=0.5, sample_rate=22050)
    assert len(signal) == int(round(22050 * 0.5))


def test_karplus_strong_pluck_is_not_silent():
    signal = karplus_strong_pluck(freq=196.0, duration_s=0.5, sample_rate=22050, seed=1)
    assert np.max(np.abs(signal)) > 0.05


def test_karplus_strong_pluck_decays_over_time():
    # A plucked string's amplitude envelope should trend downward: compare
    # RMS energy in the first vs. last third of a long-enough note.
    signal = karplus_strong_pluck(freq=110.0, duration_s=1.5, sample_rate=22050, seed=2)
    third = len(signal) // 3
    early_rms = np.sqrt(np.mean(signal[:third] ** 2))
    late_rms = np.sqrt(np.mean(signal[-third:] ** 2))
    assert late_rms < early_rms


def test_karplus_strong_pluck_is_deterministic_given_a_seed():
    a = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=42)
    b = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=42)
    np.testing.assert_array_equal(a, b)


def test_karplus_strong_pluck_seeds_differ():
    a = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=1)
    b = karplus_strong_pluck(freq=146.83, duration_s=0.3, sample_rate=22050, seed=2)
    assert not np.array_equal(a, b)


def test_scale_pitches_c_major_matches_known_semitones():
    # C major: C D E F G A B C -> semitone offsets 0 2 4 5 7 9 11 12 from C4 (60)
    assert scale_pitches("C major", tonic_midi_base=60) == [60, 62, 64, 65, 67, 69, 71, 72]


def test_scale_pitches_a_minor_matches_known_semitones():
    # A minor: A B C D E F G A -> tonic A4 = 69, offsets 0 2 3 5 7 8 10 12
    assert scale_pitches("A minor", tonic_midi_base=60) == [69, 71, 72, 74, 76, 77, 79, 81]


def test_scale_pitches_respects_flat_tonic_name():
    # B- major (music21 convention for B-flat major)
    pitches = scale_pitches("B- major", tonic_midi_base=60)
    assert pitches[0] == 70  # B-flat is 10 semitones above C
