import pytest

from score_schema.edits import EditError, apply_edit


def _score(events=None, meter="4/4", tempo=120.0):
    spb = 60.0 / tempo
    default_events = [{
        "id": "note_00", "pitch": 52, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None,
    }]
    return {
        "schemaVersion": 4,
        "timeMap": [{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": spb}],
        "parts": [{
            "instrument": "guitar", "tempoBpm": tempo, "meter": meter, "key": "E minor",
            "confidence": {"tempo": 0.9, "meter": 0.8, "key": 0.7},
            "measures": [{"number": 1, "events": events or default_events}],
        }],
    }


def test_set_pitch_changes_pitch_and_locks_without_mutating_input():
    score = _score()
    out = apply_edit(score, {"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    ev = out["parts"][0]["measures"][0]["events"][0]
    assert ev["pitch"] == 60 and ev["locked"] is True
    assert score["parts"][0]["measures"][0]["events"][0]["pitch"] == 52  # input untouched


def test_move_note_recomputes_seconds_from_time_map():
    score = _score()  # 120 bpm -> 0.5 s/beat
    out = apply_edit(score, {"type": "move_note", "eventId": "note_00", "notatedOnset": "1/4"})
    ev = out["parts"][0]["measures"][0]["events"][0]
    assert ev["notatedOnset"] == "1/4"          # beat 1 of measure 1
    assert ev["onsetSeconds"] == pytest.approx(0.5)
    assert ev["offsetSeconds"] == pytest.approx(1.0)  # duration 1/4 whole = 1 beat


def test_move_note_beyond_measure_rejected():
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "move_note", "eventId": "note_00", "notatedOnset": "9/8"})


def test_delete_then_unknown_event_rejected():
    out = apply_edit(_score(), {"type": "delete_note", "eventId": "note_00"})
    assert out["parts"][0]["measures"][0]["events"] == []
    with pytest.raises(EditError):
        apply_edit(out, {"type": "set_pitch", "eventId": "note_00", "pitch": 60})


def test_add_note_generates_id_and_seconds_and_requires_existing_measure():
    out = apply_edit(_score(), {"type": "add_note", "measureNumber": 1,
                                "notatedOnset": "1/2", "notatedDuration": "1/4",
                                "pitch": 64, "voice": 1})
    events = out["parts"][0]["measures"][0]["events"]
    added = [e for e in events if e["pitch"] == 64][0]
    assert added["locked"] is True and added["onsetSeconds"] == pytest.approx(1.0)
    assert added["id"] not in {"note_00"}
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "add_note", "measureNumber": 7,
                              "notatedOnset": "0/1", "notatedDuration": "1/4",
                              "pitch": 64, "voice": 1})


def test_set_fingering_and_hand_validate_instrument():
    out = apply_edit(_score(), {"type": "set_fingering", "eventId": "note_00",
                                "string": 4, "fret": 7})
    ev = out["parts"][0]["measures"][0]["events"][0]
    assert (ev["string"], ev["fret"], ev["locked"]) == (4, 7, True)
    with pytest.raises(EditError):  # hand op on a guitar part
        apply_edit(_score(), {"type": "set_hand", "eventId": "note_00", "hand": "left"})


def test_set_part_fact_tempo_rescales_all_seconds():
    out = apply_edit(_score(), {"type": "set_part_fact", "field": "tempoBpm", "value": 60.0})
    part = out["parts"][0]
    assert part["tempoBpm"] == 60.0
    assert out["timeMap"][1]["seconds"] == pytest.approx(1.0)
    ev = part["measures"][0]["events"][0]
    assert ev["onsetSeconds"] == pytest.approx(0.0) and ev["offsetSeconds"] == pytest.approx(1.0)


def test_set_part_fact_meter_rebuckets_measures():
    events = [
        {"id": f"note_{i:02d}", "pitch": 52, "onsetSeconds": i * 0.5, "offsetSeconds": i * 0.5 + 0.5,
         "notatedOnset": f"{i}/4", "notatedDuration": "1/4", "voice": 1,
         "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None}
        for i in range(4)  # beats 0..3 of one 4/4 measure
    ]
    out = apply_edit(_score(events=events), {"type": "set_part_fact", "field": "meter", "value": "3/4"})
    measures = out["parts"][0]["measures"]
    assert [m["number"] for m in measures] == [1, 2]
    assert len(measures[0]["events"]) == 3 and len(measures[1]["events"]) == 1
    assert measures[1]["events"][0]["notatedOnset"] == "0/1"  # beat 3 -> measure 2 beat 0


def test_invalid_op_type_and_pitch_bounds_rejected():
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "explode"})
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "set_pitch", "eventId": "note_00", "pitch": 128})
