"""Export round-trip coverage for every meter in score_schema.meters.SUPPORTED_METERS
(spec docs/superpowers/specs/2026-08-19-meter-expansion-design.md §5, plan Task 5).

Builders mirror packages/musicxml/tests/test_export.py's _sample_score /
_piano_score helpers (the existing tests are the authority for the real
exporter entry point and score-dict shape) rather than the plan's
pseudocode `export_music_xml` name, which does not exist in this package.

Note: music21 does NOT preserve "2/2" as TimeSignature.ratioString — it
normalizes 2/2 to cut time and ratioString comes back as "2/2" only
coincidentally for *some* music21 versions; to avoid depending on that
formatting detail we assert on numerator/denominator throughout, per the
plan's explicit guidance for this exact risk.
"""
from pathlib import Path

import pytest
from music21 import converter

from score_schema.meters import SUPPORTED_METERS
from score_schema.models import build_score

from musicxml.export import score_json_to_musicxml


def _measure_length_ql(meter: str) -> float:
    numerator_str, denominator_str = meter.split("/")
    return int(numerator_str) * (4.0 / int(denominator_str))


def _note_event(id_: str, pitch: int, hand: str | None = None) -> dict:
    # Duration "1/16" of a whole note = 0.25 quarterLength — smaller than
    # the shortest supported measure (3/8 = 1.5 ql) so a single event never
    # needs export.py's overlap/bar-crossing clamp to fit.
    event = {
        "id": id_, "pitch": pitch, "onsetSeconds": 0.0, "offsetSeconds": 0.125,
        "notatedOnset": "0/1", "notatedDuration": "1/16", "voice": 1,
        "confidence": 0.9, "locked": False,
    }
    if hand is not None:
        event["hand"] = hand
    return event


@pytest.fixture
def guitar_score_builder():
    def _build(meter: str) -> dict:
        return build_score(
            instrument="guitar",
            tempo_bpm=120.0,
            meter=meter,
            key="C major",
            confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
            time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
            measures=[{"number": 1, "events": [_note_event("note_00", 64)]}],
        )

    return _build


@pytest.fixture
def piano_score_builder():
    def _build(meter: str) -> dict:
        return build_score(
            instrument="piano",
            tempo_bpm=120.0,
            meter=meter,
            key="C major",
            confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
            time_map=[{"beat": 0, "seconds": 0.0}],
            measures=[
                {
                    "number": 1,
                    "events": [
                        _note_event("note_left", 40, hand="left"),
                        _note_event("note_right", 76, hand="right"),
                    ],
                }
            ],
        )

    return _build


@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_time_signature_round_trips_guitar(meter, guitar_score_builder, tmp_path: Path):
    score = guitar_score_builder(meter)
    out_path = tmp_path / f"guitar_{meter.replace('/', '-')}.musicxml"
    score_json_to_musicxml(score, out_path)

    parsed = converter.parse(str(out_path))
    numerator, denominator = (int(part) for part in meter.split("/"))

    signatures = {
        (ts.numerator, ts.denominator) for ts in parsed.recurse().getElementsByClass("TimeSignature")
    }
    assert signatures == {(numerator, denominator)}

    measures = parsed.recurse().getElementsByClass("Measure")
    assert len(measures) >= 1
    for measure in measures:
        assert measure.duration.quarterLength == _measure_length_ql(meter)


@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_time_signature_round_trips_piano(meter, piano_score_builder, tmp_path: Path):
    score = piano_score_builder(meter)
    out_path = tmp_path / f"piano_{meter.replace('/', '-')}.musicxml"
    score_json_to_musicxml(score, out_path)

    parsed = converter.parse(str(out_path))
    numerator, denominator = (int(part) for part in meter.split("/"))

    signatures = {
        (ts.numerator, ts.denominator) for ts in parsed.recurse().getElementsByClass("TimeSignature")
    }
    assert signatures == {(numerator, denominator)}

    measures = parsed.recurse().getElementsByClass("Measure")
    assert len(measures) >= 1
    for measure in measures:
        assert measure.duration.quarterLength == _measure_length_ql(meter)
