from __future__ import annotations

import copy
import math
import re
import uuid
from fractions import Fraction

from score_schema.validate import ScoreValidationError, validate_score

# Mirrors aura_worker.stages.structure.METER_CANDIDATES keys (copied so this
# package stays standalone); verify against that file and keep in sync.
_ALLOWED_METERS = ("4/4", "3/4")

# Mirrors the key pattern from score_schema.validate._SCORE_SCHEMA
_KEY_PATTERN = re.compile(r"^[A-G](#|-)? (major|minor)$")


class EditError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def beats_per_measure(meter: str) -> Fraction:
    num, den = meter.split("/")
    return Fraction(int(num)) * Fraction(4, int(den))


def seconds_per_beat(time_map: list[dict]) -> float:
    b0, b1 = time_map[0], time_map[1]
    return (b1["seconds"] - b0["seconds"]) / (b1["beat"] - b0["beat"])


def _require(op: dict, key: str) -> None:
    """Raise EditError if required key is missing from op."""
    if key not in op:
        raise EditError(f"missing required op key: {key!r}")


def _validate_key(key: str) -> None:
    """Validate key format against the schema pattern."""
    if not isinstance(key, str) or not key.strip():
        raise EditError("key must be a non-empty string")
    if not _KEY_PATTERN.match(key):
        raise EditError(f"key must match pattern [A-G][#-]? (major|minor), got {key!r}")


def _fraction(text: str, what: str) -> Fraction:
    if not isinstance(text, str):
        raise EditError(f"{what} must be a string, got {type(text).__name__}")
    try:
        num, den = text.split("/")
        f = Fraction(int(num), int(den))
    except (ValueError, ZeroDivisionError) as exc:
        raise EditError(f"invalid {what}: {text!r}") from exc
    if f < 0:
        raise EditError(f"{what} must be >= 0")
    return f


def _find_event(part: dict, event_id: str) -> tuple[dict, dict]:
    for measure in part["measures"]:
        for event in measure["events"]:
            if event["id"] == event_id:
                return measure, event
    raise EditError(f"unknown event: {event_id}")


def _retime(score: dict, part: dict, measure: dict, event: dict) -> None:
    """Recompute onsetSeconds/offsetSeconds from notated position + timeMap."""
    spb = seconds_per_beat(score["timeMap"])
    bpm_frac = beats_per_measure(part["meter"])
    onset_beats = _fraction(event["notatedOnset"], "notatedOnset") * 4
    duration_beats = _fraction(event["notatedDuration"], "notatedDuration") * 4
    absolute = (measure["number"] - 1) * bpm_frac + onset_beats
    event["onsetSeconds"] = float(absolute) * spb
    event["offsetSeconds"] = float(absolute + duration_beats) * spb


def _rebucket(part: dict, old_meter: str, new_meter: str) -> None:
    """Reassign events to measures for a new meter, preserving absolute beats.

    Emits every measure number 1..max, not just numbers that ended up with
    an event — matching quantize.py's silent-measure fidelity fix. Without
    this, a measure that is (or becomes, e.g. via delete_note removing its
    only event) pure silence would vanish from the rebucketed score instead
    of surviving as an empty-events entry, and the musical duration
    represented by the old measure range would silently shrink.
    """
    old_bpm = beats_per_measure(old_meter)
    new_bpm = beats_per_measure(new_meter)
    old_max_number = max((measure["number"] for measure in part["measures"]), default=0)

    flat: list[tuple[Fraction, dict]] = []
    for measure in part["measures"]:
        for event in measure["events"]:
            onset_beats = _fraction(event["notatedOnset"], "notatedOnset") * 4
            flat.append(((measure["number"] - 1) * old_bpm + onset_beats, event))
    flat.sort(key=lambda pair: pair[0])
    buckets: dict[int, list[dict]] = {}
    for absolute, event in flat:
        number = int(absolute // new_bpm) + 1
        within = absolute - (number - 1) * new_bpm
        whole = within / 4
        event["notatedOnset"] = f"{whole.numerator}/{whole.denominator}"
        buckets.setdefault(number, []).append(event)

    # The old measure range's full musical duration must still be covered
    # even where nothing landed: the end of the old last measure, converted
    # into the new meter's beat count and rounded up (a partial new measure
    # at the boundary still counts as a whole one).
    preserved_max_number = 0
    if old_max_number > 0:
        preserved_max_number = math.ceil(old_max_number * old_bpm / new_bpm)
    max_number = max(max(buckets.keys(), default=0), preserved_max_number)

    part["measures"] = [
        {"number": number, "events": buckets.get(number, [])}
        for number in range(1, max_number + 1)
    ]


def apply_edit(score: dict, op: dict) -> dict:
    out = copy.deepcopy(score)
    part = out["parts"][0]
    kind = op.get("type")

    if kind == "set_pitch":
        _require(op, "eventId")
        if not isinstance(op.get("pitch"), int) or not 0 <= op["pitch"] <= 127:
            raise EditError("pitch must be an integer 0-127")
        _, event = _find_event(part, op["eventId"])
        event["pitch"] = op["pitch"]
        event["locked"] = True

    elif kind == "move_note":
        _require(op, "eventId")
        _require(op, "notatedOnset")
        measure, event = _find_event(part, op["eventId"])
        onset_beats = _fraction(op["notatedOnset"], "notatedOnset") * 4
        if onset_beats >= beats_per_measure(part["meter"]):
            raise EditError("notatedOnset outside the measure")
        event["notatedOnset"] = op["notatedOnset"]
        event["locked"] = True
        _retime(out, part, measure, event)

    elif kind == "set_duration":
        _require(op, "eventId")
        _require(op, "notatedDuration")
        duration_beats = _fraction(op["notatedDuration"], "notatedDuration") * 4
        if duration_beats <= 0:
            raise EditError("notatedDuration must be > 0")
        measure, event = _find_event(part, op["eventId"])
        event["notatedDuration"] = op["notatedDuration"]
        event["locked"] = True
        _retime(out, part, measure, event)

    elif kind == "delete_note":
        _require(op, "eventId")
        measure, event = _find_event(part, op["eventId"])
        measure["events"].remove(event)

    elif kind == "add_note":
        _require(op, "measureNumber")
        _require(op, "notatedOnset")
        _require(op, "notatedDuration")
        _require(op, "pitch")
        if not isinstance(op.get("measureNumber"), int):
            raise EditError("measureNumber must be an integer")
        numbers = {m["number"]: m for m in part["measures"]}
        measure = numbers.get(op["measureNumber"])
        if measure is None:
            raise EditError(f"measure {op['measureNumber']} does not exist")
        if not isinstance(op.get("pitch"), int) or not 0 <= op["pitch"] <= 127:
            raise EditError("pitch must be an integer 0-127")
        onset_beats = _fraction(op["notatedOnset"], "notatedOnset") * 4
        if onset_beats >= beats_per_measure(part["meter"]):
            raise EditError("notatedOnset outside the measure")
        event = {
            "id": f"note_{uuid.uuid4().hex[:8]}",
            "pitch": op["pitch"], "onsetSeconds": 0.0, "offsetSeconds": 0.0,
            "notatedOnset": op["notatedOnset"], "notatedDuration": op["notatedDuration"],
            "voice": op.get("voice", 1), "confidence": 1.0, "locked": True,
            "string": None, "fret": None, "hand": None,
        }
        _retime(out, part, measure, event)
        measure["events"].append(event)
        measure["events"].sort(key=lambda e: _fraction(e["notatedOnset"], "notatedOnset"))

    elif kind == "set_fingering":
        _require(op, "eventId")
        _require(op, "string")
        _require(op, "fret")
        if part["instrument"] != "guitar":
            raise EditError("set_fingering only applies to guitar parts")
        if not isinstance(op.get("string"), int) or not 0 <= op["string"] <= 5:
            raise EditError("string must be an integer 0-5")
        if not isinstance(op.get("fret"), int) or not 0 <= op["fret"] <= 20:
            raise EditError("fret must be an integer 0-20")
        _, event = _find_event(part, op["eventId"])
        event["string"], event["fret"], event["locked"] = op["string"], op["fret"], True

    elif kind == "set_hand":
        _require(op, "eventId")
        _require(op, "hand")
        if part["instrument"] != "piano":
            raise EditError("set_hand only applies to piano parts")
        if op.get("hand") not in ("left", "right"):
            raise EditError("hand must be 'left' or 'right'")
        _, event = _find_event(part, op["eventId"])
        event["hand"], event["locked"] = op["hand"], True

    elif kind == "set_locked":
        _require(op, "eventId")
        _require(op, "locked")
        if not isinstance(op.get("locked"), bool):
            raise EditError("locked must be a boolean")
        _, event = _find_event(part, op["eventId"])
        event["locked"] = op["locked"]

    elif kind == "set_part_fact":
        field, value = op.get("field"), op.get("value")
        if field == "tempoBpm":
            if not isinstance(value, (int, float)) or not 20 <= value <= 300:
                raise EditError("tempoBpm must be a number 20-300")
            part["tempoBpm"] = float(value)
            spb = 60.0 / float(value)
            out["timeMap"] = [{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": spb}]
            for measure in part["measures"]:
                for event in measure["events"]:
                    _retime(out, part, measure, event)
        elif field == "meter":
            if value not in _ALLOWED_METERS:
                raise EditError(f"meter must be one of {_ALLOWED_METERS}")
            old_meter = part["meter"]
            part["meter"] = value
            _rebucket(part, old_meter, value)
            for measure in part["measures"]:
                for event in measure["events"]:
                    _retime(out, part, measure, event)
        elif field == "key":
            _validate_key(value)
            part["key"] = value
        else:
            raise EditError(f"unknown part fact: {field}")

    else:
        raise EditError(f"unknown edit type: {kind}")

    try:
        validate_score(out)
    except ScoreValidationError as exc:
        raise EditError(f"edit produces an invalid score: {exc}") from exc
    return out
