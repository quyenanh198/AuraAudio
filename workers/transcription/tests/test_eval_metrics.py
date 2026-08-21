from aura_worker.eval.metrics import (
    key_matches,
    meter_matches,
    midi_to_hz,
    onset_f1,
    onset_offset_f1,
    tempo_within_tolerance,
)
from test_fixtures.reference import ReferenceEvent


def _ref(pitch, onset_s, offset_s):
    return ReferenceEvent(pitch=pitch, onset_s=onset_s, offset_s=offset_s)


def test_midi_to_hz_a4_is_440():
    assert abs(midi_to_hz(69) - 440.0) < 1e-6


def test_midi_to_hz_octave_doubles_frequency():
    assert abs(midi_to_hz(81) - 880.0) < 1e-6


def test_onset_f1_perfect_match_scores_1():
    ref = [_ref(60, 0.0, 0.5), _ref(64, 0.5, 1.0), _ref(67, 1.0, 1.5)]
    est = [_ref(60, 0.0, 0.5), _ref(64, 0.5, 1.0), _ref(67, 1.0, 1.5)]

    result = onset_f1(ref, est)

    assert result.precision == 1.0
    assert result.recall == 1.0
    assert result.f1 == 1.0


def test_onset_f1_within_tolerance_still_matches():
    ref = [_ref(60, 0.500, 1.0)]
    est = [_ref(60, 0.530, 1.0)]  # 30ms off, within the default 50ms tolerance

    result = onset_f1(ref, est, onset_tolerance_s=0.05)

    assert result.f1 == 1.0


def test_onset_f1_outside_tolerance_does_not_match():
    ref = [_ref(60, 0.500, 1.0)]
    est = [_ref(60, 0.600, 1.0)]  # 100ms off, outside a 50ms tolerance

    result = onset_f1(ref, est, onset_tolerance_s=0.05)

    assert result.f1 == 0.0


def test_onset_f1_wrong_pitch_does_not_match():
    ref = [_ref(60, 0.0, 0.5)]
    est = [_ref(61, 0.0, 0.5)]  # 1 semitone off -> outside default 50-cent tolerance

    result = onset_f1(ref, est)

    assert result.f1 == 0.0


def test_onset_f1_extra_estimated_note_hurts_precision_not_recall():
    ref = [_ref(60, 0.0, 0.5)]
    est = [_ref(60, 0.0, 0.5), _ref(72, 2.0, 2.5)]  # one correct + one spurious

    result = onset_f1(ref, est)

    assert result.recall == 1.0
    assert result.precision == 0.5


def test_onset_f1_missing_estimated_note_hurts_recall_not_precision():
    ref = [_ref(60, 0.0, 0.5), _ref(64, 1.0, 1.5)]
    est = [_ref(60, 0.0, 0.5)]

    result = onset_f1(ref, est)

    assert result.precision == 1.0
    assert result.recall == 0.5


def test_onset_f1_empty_estimate_scores_zero_not_crash():
    ref = [_ref(60, 0.0, 0.5)]
    result = onset_f1(ref, [])
    assert result.f1 == 0.0


def test_onset_offset_f1_requires_matching_offset_too():
    ref = [_ref(60, 0.0, 1.0)]
    est_good_offset = [_ref(60, 0.0, 1.02)]  # well within the 20%-of-duration tolerance
    est_bad_offset = [_ref(60, 0.0, 2.0)]  # offset off by 1s, well outside tolerance

    assert onset_offset_f1(ref, est_good_offset).f1 == 1.0
    assert onset_offset_f1(ref, est_bad_offset).f1 == 0.0


def test_onset_offset_f1_is_stricter_than_onset_only_f1():
    ref = [_ref(60, 0.0, 1.0)]
    est = [_ref(60, 0.0, 3.0)]  # onset matches, offset wildly off

    assert onset_f1(ref, est).f1 == 1.0
    assert onset_offset_f1(ref, est).f1 == 0.0


def test_tempo_within_tolerance_accepts_within_5_percent():
    assert tempo_within_tolerance(126.0, 120.0, rel_tol=0.05) is True
    assert tempo_within_tolerance(114.0, 120.0, rel_tol=0.05) is True


def test_tempo_within_tolerance_rejects_beyond_5_percent():
    assert tempo_within_tolerance(130.0, 120.0, rel_tol=0.05) is False


def test_key_matches_is_case_insensitive_exact_match():
    assert key_matches("C major", "c major") is True
    assert key_matches("C major", "C minor") is False
    assert key_matches("D major", "C major") is False


def test_meter_matches_exact_string():
    assert meter_matches("4/4", "4/4") is True
    assert meter_matches("3/4", "4/4") is False
