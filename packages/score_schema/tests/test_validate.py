import pytest

from score_schema.models import build_score
from score_schema.validate import ScoreValidationError, validate_score


def _valid_score():
    return build_score(
        instrument="piano",
        tempo_bpm=100.0,
        meter="3/4",
        key="A minor",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.6},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_01",
                        "pitch": 60,
                        "onsetSeconds": 0.0,
                        "offsetSeconds": 0.5,
                        "notatedOnset": "0/1",
                        "notatedDuration": "1/4",
                        "voice": 1,
                        "confidence": 0.8,
                        "locked": False,
                    }
                ],
            }
        ],
    )


def test_valid_score_passes():
    validate_score(_valid_score())  # must not raise


def test_missing_pitch_is_rejected():
    score = _valid_score()
    del score["parts"][0]["measures"][0]["events"][0]["pitch"]
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_out_of_range_confidence_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["confidence"] = 1.5
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_schema_v1_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 1
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_part_missing_tempo_bpm_is_rejected():
    score = _valid_score()
    del score["parts"][0]["tempoBpm"]
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_part_missing_confidence_is_rejected():
    score = _valid_score()
    del score["parts"][0]["confidence"]
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_meter_outside_candidate_set_is_rejected():
    score = _valid_score()
    score["parts"][0]["meter"] = "13/16"
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_flat_key_with_music21_notation_is_accepted():
    score = _valid_score()
    score["parts"][0]["key"] = "B- major"
    validate_score(score)  # must not raise — "-" is music21's native flat notation


def test_schema_v2_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 2
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_event_without_string_or_fret_is_accepted():
    validate_score(_valid_score())  # _valid_score()'s event has no string/fret keys at all


def test_event_with_null_string_and_fret_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = None
    score["parts"][0]["measures"][0]["events"][0]["fret"] = None
    validate_score(score)  # must not raise


def test_event_with_valid_string_and_fret_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = 2
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 5
    validate_score(score)  # must not raise


def test_event_with_out_of_range_string_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = 6
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 0
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_event_with_out_of_range_fret_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = 0
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 21
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_schema_v3_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 3
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_event_without_hand_is_accepted():
    validate_score(_valid_score())  # _valid_score()'s event has no hand key at all


def test_event_with_null_hand_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["hand"] = None
    validate_score(score)  # must not raise


def test_event_with_valid_hand_values_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["hand"] = "left"
    validate_score(score)  # must not raise
    score["parts"][0]["measures"][0]["events"][0]["hand"] = "right"
    validate_score(score)  # must not raise


def test_event_with_invalid_hand_value_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["hand"] = "both"
    with pytest.raises(ScoreValidationError):
        validate_score(score)
