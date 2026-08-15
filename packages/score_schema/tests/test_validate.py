import pytest

from score_schema.models import build_score
from score_schema.validate import ScoreValidationError, validate_score


def _valid_score():
    return build_score(
        instrument="piano",
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


def test_wrong_schema_version_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 2
    with pytest.raises(ScoreValidationError):
        validate_score(score)
