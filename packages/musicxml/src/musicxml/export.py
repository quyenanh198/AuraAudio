# packages/musicxml/src/musicxml/export.py — full replacement
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from music21 import articulations, duration, instrument, key as m21_key, meter as m21_meter, note, pitch as m21_pitch, stream, tempo


def _notated_fraction_to_quarter_length(value: str) -> float:
    """A notated value like '1/4' means one quarter of a whole note (a
    quarter note = 1.0 quarterLength in music21), so quarterLength = 4 * fraction."""
    return float(Fraction(value) * 4)


def _spell_pitch(midi_number: int, key_obj: m21_key.Key) -> m21_pitch.Pitch:
    """Spell a MIDI pitch using the detected key's diatonic collection where
    possible, falling back to the key's sharp/flat preference for chromatic
    (non-diatonic) tones."""
    pc = midi_number % 12
    diatonic_by_pc = {p.pitchClass: p.name for p in key_obj.pitches[:7]}
    if pc in diatonic_by_pc:
        p = m21_pitch.Pitch(diatonic_by_pc[pc])
        p.octave = 4
        p.octave += round((midi_number - p.midi) / 12)
        return p

    default = m21_pitch.Pitch(ps=midi_number)  # sharp-preferred by default
    if key_obj.sharps < 0 and default.accidental is not None and default.accidental.name == "sharp":
        return default.getEnharmonic()
    return default


def score_json_to_musicxml(score: dict, out_path: Path) -> Path:
    part_data = score["parts"][0]
    tonic_name, mode = part_data["key"].split(" ")
    key_obj = m21_key.Key(tonic_name, mode)

    m21_part = stream.Part()
    m21_part.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    m21_part.insert(0, key_obj)
    m21_part.insert(0, instrument.Guitar() if part_data["instrument"] == "guitar" else instrument.Piano())

    is_first_measure = True
    for measure_data in part_data["measures"]:
        m21_measure = stream.Measure(number=measure_data["number"])
        if is_first_measure:
            # MetronomeMark must live in the Measure, not the Part — see the
            # "Validated design notes" in the implementation plan: inserting
            # it at the Part level silently drops it from the exported XML.
            m21_measure.insert(0, tempo.MetronomeMark(number=part_data["tempoBpm"]))
            is_first_measure = False
        for event in measure_data["events"]:
            n = note.Note(_spell_pitch(event["pitch"], key_obj))
            n.duration = duration.Duration(_notated_fraction_to_quarter_length(event["notatedDuration"]))
            internal_string = event.get("string")
            fret = event.get("fret")
            if internal_string is not None and fret is not None:
                musicxml_string = 6 - internal_string
                n.articulations.append(articulations.StringIndication(musicxml_string))
                n.articulations.append(articulations.FretIndication(fret))
            m21_measure.append(n)
        m21_part.append(m21_measure)

    m21_score = stream.Score()
    m21_score.insert(0, m21_part)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m21_score.write("musicxml", fp=str(out_path))
    return out_path
