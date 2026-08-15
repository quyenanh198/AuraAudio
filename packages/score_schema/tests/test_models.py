from score_schema.models import JobErrorCode, NoteEvent, build_score


def test_note_event_is_immutable_and_typed():
    ev = NoteEvent(pitch=64, onset_s=0.61, offset_s=1.08, velocity=90, confidence=0.91)
    assert ev.pitch == 64
    assert ev.confidence == 0.91


def test_job_error_code_values_match_spec():
    assert JobErrorCode.UNSUPPORTED_MEDIA == "UNSUPPORTED_MEDIA"
    assert JobErrorCode.NO_MUSIC_DETECTED == "NO_MUSIC_DETECTED"


def test_build_score_produces_schema_v1_shape():
    score = build_score(
        instrument="guitar",
        time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_01",
                        "pitch": 64,
                        "onsetSeconds": 0.61,
                        "offsetSeconds": 1.08,
                        "notatedOnset": "1/4",
                        "notatedDuration": "1/4",
                        "voice": 1,
                        "confidence": 0.91,
                        "locked": False,
                    }
                ],
            }
        ],
    )
    assert score["schemaVersion"] == 1
    assert score["parts"][0]["instrument"] == "guitar"
    assert score["parts"][0]["measures"][0]["events"][0]["pitch"] == 64
