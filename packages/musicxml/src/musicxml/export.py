from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from music21 import duration, instrument, meter, note, stream, tempo


def _notated_fraction_to_quarter_length(value: str) -> float:
    """A notated value like '1/4' means one quarter of a whole note (a
    quarter note = 1.0 quarterLength in music21), so quarterLength = 4 * fraction."""
    return float(Fraction(value) * 4)


def score_json_to_musicxml(score: dict, out_path: Path) -> Path:
    part_data = score["parts"][0]
    m21_part = stream.Part()
    m21_part.insert(0, meter.TimeSignature("4/4"))
    m21_part.insert(0, tempo.MetronomeMark(number=120))
    m21_part.insert(0, instrument.Guitar() if part_data["instrument"] == "guitar" else instrument.Piano())

    for measure_data in part_data["measures"]:
        m21_measure = stream.Measure(number=measure_data["number"])
        for event in measure_data["events"]:
            n = note.Note(event["pitch"])
            n.duration = duration.Duration(_notated_fraction_to_quarter_length(event["notatedDuration"]))
            m21_measure.append(n)
        m21_part.append(m21_measure)

    m21_score = stream.Score()
    m21_score.insert(0, m21_part)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m21_score.write("musicxml", fp=str(out_path))
    return out_path
