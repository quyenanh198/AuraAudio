import pytest

from score_schema.edits import EditError, apply_edit
from score_schema.meters import SUPPORTED_METERS


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


def test_add_note_into_empty_measure_succeeds():
    """Known editing gap this closes: add_note into a measure that exists
    but currently has zero events (a silent measure, now emitted by
    quantize.py's silent-measure fidelity fix) must succeed, not 422."""
    score = _score()
    score["parts"][0]["measures"] = [
        {"number": 1, "events": score["parts"][0]["measures"][0]["events"]},
        {"number": 2, "events": []},
    ]
    out = apply_edit(score, {"type": "add_note", "measureNumber": 2,
                              "notatedOnset": "0/1", "notatedDuration": "1/4",
                              "pitch": 67, "voice": 1})
    events = out["parts"][0]["measures"][1]["events"]
    assert len(events) == 1
    assert events[0]["pitch"] == 67 and events[0]["locked"] is True


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


def test_rebucket_preserves_interior_and_trailing_silent_measures():
    """Measure 2 is already silent (post-quantize fidelity fix) and the
    span of old measure 3 partially overflows into a new measure 4 once
    rebucketed to a shorter meter — both must survive as empty-events
    entries, not be dropped."""
    events = [
        {"id": "note_00", "pitch": 52, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
         "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
         "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None},
    ]
    events2 = [
        {"id": "note_01", "pitch": 55, "onsetSeconds": 4.0, "offsetSeconds": 4.5,
         "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
         "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None},
    ]
    score = _score(events=events)
    score["parts"][0]["measures"] = [
        {"number": 1, "events": events},
        {"number": 2, "events": []},
        {"number": 3, "events": events2},
    ]
    out = apply_edit(score, {"type": "set_part_fact", "field": "meter", "value": "3/4"})
    measures = out["parts"][0]["measures"]
    assert [m["number"] for m in measures] == [1, 2, 3, 4]
    assert len(measures[0]["events"]) == 1  # abs beat 0 -> measure 1
    assert measures[1]["events"] == []  # interior gap preserved
    assert len(measures[2]["events"]) == 1  # abs beat 8 -> measure 3
    assert measures[3]["events"] == []  # trailing span of old measure 3 overflow


@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_set_part_fact_accepts_every_supported_meter(meter):
    result = apply_edit(_score(), {"type": "set_part_fact", "field": "meter", "value": meter})
    assert result["parts"][0]["meter"] == meter


def test_set_part_fact_rejects_unsupported_meter():
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "set_part_fact", "field": "meter", "value": "13/16"})


def test_meter_rebucket_round_trip_4_4_to_6_8_and_back():
    base_score = _score()
    to_68 = apply_edit(base_score, {"type": "set_part_fact", "field": "meter", "value": "6/8"})
    back = apply_edit(to_68, {"type": "set_part_fact", "field": "meter", "value": "4/4"})
    orig_events = [
        (e["id"], e["notatedOnset"], e["notatedDuration"])
        for m in base_score["parts"][0]["measures"] for e in m["events"]
    ]
    back_events = [
        (e["id"], e["notatedOnset"], e["notatedDuration"])
        for m in back["parts"][0]["measures"] for e in m["events"]
    ]
    assert back_events == orig_events


def test_rebucket_does_not_drop_measure_after_delete_all_notes_then_meter_change():
    """Known deferred gap (last review): delete_note emptying a measure's
    only event, followed by a meter change, used to drop the measure
    entirely because _rebucket only rebuilt measures from surviving
    events. It must now still exist as an empty-events entry."""
    out = apply_edit(_score(), {"type": "delete_note", "eventId": "note_00"})
    assert out["parts"][0]["measures"] == [{"number": 1, "events": []}]

    out2 = apply_edit(out, {"type": "set_part_fact", "field": "meter", "value": "3/4"})
    measures = out2["parts"][0]["measures"]
    # Old measure 1 (4/4, 4 beats) spans new measures 1-2 (3/4, 3 beats each).
    assert [m["number"] for m in measures] == [1, 2]
    assert measures[0]["events"] == [] and measures[1]["events"] == []


def test_invalid_op_type_and_pitch_bounds_rejected():
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "explode"})
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "set_pitch", "eventId": "note_00", "pitch": 128})


def test_missing_eventid_raises_edit_error():
    with pytest.raises(EditError) as exc_info:
        apply_edit(_score(), {"type": "set_pitch", "pitch": 60})
    assert exc_info.value.reason == "missing required op key: 'eventId'"


def test_invalid_key_raises_edit_error():
    with pytest.raises(EditError) as exc_info:
        apply_edit(_score(), {"type": "set_part_fact", "field": "key", "value": "nonsense"})
    assert "key must match pattern" in exc_info.value.reason


def test_add_note_with_voice_zero_rejected():
    """Voice 0 fails schema validation (voice >= 1); should raise EditError, not ScoreValidationError."""
    with pytest.raises(EditError) as exc_info:
        apply_edit(_score(), {"type": "add_note", "measureNumber": 1,
                              "notatedOnset": "0/1", "notatedDuration": "1/4",
                              "pitch": 64, "voice": 0})
    assert "invalid score" in exc_info.value.reason


def _piano_score():
    return {
        "schemaVersion": 4,
        "timeMap": [{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        "parts": [{
            "instrument": "piano", "tempoBpm": 120.0, "meter": "4/4", "key": "C major",
            "confidence": {"tempo": 0.9, "meter": 0.8, "key": 0.7},
            "measures": [{"number": 1, "events": [{
                "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                "confidence": 0.9, "locked": False, "string": None, "fret": None, "hand": None,
            }]}],
        }],
    }


# Wrong-typed values grouped by what the field actually expects. Each group
# covers "int where str expected, null, dict, list" (or the analogous
# str-where-int/bool-where-*) so every typed op field gets swept.
_BAD_STR_VALUES = (5, None, {"x": 1}, [1, 2])
_BAD_INT_VALUES = ("5", None, {"x": 1}, [1, 2])
_BAD_BOOL_VALUES = ("true", None, {"x": 1}, [1, 2], 1)


def _wrong_type_cases():
    """Build (score_factory, op) pairs covering every typed field of every op type."""
    specs = [
        (_score, {"type": "set_pitch", "eventId": "note_00", "pitch": 60},
         {"eventId": _BAD_STR_VALUES, "pitch": _BAD_INT_VALUES}),
        (_score, {"type": "move_note", "eventId": "note_00", "notatedOnset": "1/4"},
         {"eventId": _BAD_STR_VALUES, "notatedOnset": _BAD_STR_VALUES}),
        (_score, {"type": "set_duration", "eventId": "note_00", "notatedDuration": "1/4"},
         {"eventId": _BAD_STR_VALUES, "notatedDuration": _BAD_STR_VALUES}),
        (_score, {"type": "delete_note", "eventId": "note_00"},
         {"eventId": _BAD_STR_VALUES}),
        (_score, {"type": "add_note", "measureNumber": 1, "notatedOnset": "0/1",
                  "notatedDuration": "1/4", "pitch": 64},
         {"measureNumber": _BAD_INT_VALUES, "notatedOnset": _BAD_STR_VALUES,
          "notatedDuration": _BAD_STR_VALUES, "pitch": _BAD_INT_VALUES}),
        (_score, {"type": "set_fingering", "eventId": "note_00", "string": 4, "fret": 7},
         {"eventId": _BAD_STR_VALUES, "string": _BAD_INT_VALUES, "fret": _BAD_INT_VALUES}),
        (_piano_score, {"type": "set_hand", "eventId": "note_00", "hand": "left"},
         {"eventId": _BAD_STR_VALUES, "hand": _BAD_STR_VALUES}),
        (_score, {"type": "set_locked", "eventId": "note_00", "locked": True},
         {"eventId": _BAD_STR_VALUES, "locked": _BAD_BOOL_VALUES}),
        (_score, {"type": "set_part_fact", "field": "tempoBpm", "value": 90.0},
         {"value": _BAD_INT_VALUES}),
        (_score, {"type": "set_part_fact", "field": "meter", "value": "3/4"},
         {"value": _BAD_STR_VALUES}),
        (_score, {"type": "set_part_fact", "field": "key", "value": "C major"},
         {"value": _BAD_STR_VALUES}),
    ]
    cases = []
    for score_factory, base_op, field_bad_map in specs:
        for field, bad_values in field_bad_map.items():
            for bad_value in bad_values:
                op = dict(base_op)
                op[field] = bad_value
                case_id = f"{base_op['type']}:{field}={bad_value!r}"
                cases.append(pytest.param(score_factory, op, id=case_id))
    return cases


@pytest.mark.parametrize("score_factory,op", _wrong_type_cases())
def test_wrong_typed_op_fields_raise_edit_error_not_crash(score_factory, op):
    """Every op type must reject wrong-typed fields with EditError, never AttributeError/TypeError."""
    with pytest.raises(EditError):
        apply_edit(score_factory(), op)
