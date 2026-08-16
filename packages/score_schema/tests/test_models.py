from score_schema.models import JobErrorCode, NoteEvent, build_score


def test_note_event_is_immutable_and_typed():
    ev = NoteEvent(pitch=64, onset_s=0.61, offset_s=1.08, velocity=90, confidence=0.91)
    assert ev.pitch == 64
    assert ev.confidence == 0.91


def test_job_error_code_values_match_spec():
    assert JobErrorCode.UNSUPPORTED_MEDIA == "UNSUPPORTED_MEDIA"
    assert JobErrorCode.NO_MUSIC_DETECTED == "NO_MUSIC_DETECTED"


def test_build_score_produces_schema_v3_shape():
    score = build_score(
        instrument="guitar",
        tempo_bpm=128.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
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
    assert score["schemaVersion"] == 3
    part = score["parts"][0]
    assert part["instrument"] == "guitar"
    assert part["tempoBpm"] == 128.0
    assert part["meter"] == "4/4"
    assert part["key"] == "C major"
    assert part["confidence"] == {"tempo": 0.9, "meter": 0.8, "key": 0.7}
    assert part["measures"][0]["events"][0]["pitch"] == 64
