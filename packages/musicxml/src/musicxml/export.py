# packages/musicxml/src/musicxml/export.py — full replacement
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from music21 import articulations, clef, duration, instrument, key as m21_key, layout, meter as m21_meter, note, pitch as m21_pitch, stream, tempo

# packages/musicxml cannot depend on workers/transcription (packages sit
# below workers in the dependency graph) — duplicated here rather than
# imported from aura_worker.piano_hands, same reasoning as this file's
# inlined "6 - internal_string" guitar-numbering conversion.
_STANDARD_PIANO_RANGE = (21, 108)


def _notated_fraction_to_quarter_length(value: str) -> float:
    """A notated value like '1/4' means one quarter of a whole note (a
    quarter note = 1.0 quarterLength in music21), so quarterLength = 4 * fraction.

    Applies to both notatedDuration and notatedOnset — an onset is likewise
    expressed as a fraction of a whole note, measured from the measure start."""
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

    if part_data["instrument"] == "piano":
        m21_score = _build_piano_grand_staff(part_data, key_obj)
    else:
        m21_score = _build_single_staff(part_data, key_obj)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m21_score.write("musicxml", fp=str(out_path))
    return out_path


def _build_single_staff(part_data: dict, key_obj: m21_key.Key) -> stream.Score:
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
            # insert-at-onset, not append: an event list is not guaranteed to
            # be sorted by notatedOnset (real inference output is not), and
            # append() ignores notatedOnset entirely — it packs notes
            # back-to-back in list order, so exported rhythm came out in the
            # wrong order and with no rests for gaps.
            m21_measure.insert(_notated_fraction_to_quarter_length(event["notatedOnset"]), n)
        m21_part.append(m21_measure)

    m21_score = stream.Score()
    m21_score.insert(0, m21_part)
    return m21_score


def _hand_for_event(event: dict) -> str:
    """Which staff an event renders on: its assigned hand, or — for an
    out-of-range note (hand is None) — clamped to the nearer staff by
    pitch. The score JSON's hand: null is never mutated; this is a
    rendering-only fallback so no note is silently dropped from the file."""
    hand = event.get("hand")
    if hand is not None:
        return hand
    return "left" if event["pitch"] < _STANDARD_PIANO_RANGE[0] else "right"


def _build_piano_grand_staff(part_data: dict, key_obj: m21_key.Key) -> stream.Score:
    right = stream.PartStaff()
    right.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    right.insert(0, key_obj)
    right.insert(0, clef.TrebleClef())
    right.insert(0, instrument.Piano())

    left = stream.PartStaff()
    left.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    left.insert(0, key_obj)
    left.insert(0, clef.BassClef())
    left.insert(0, instrument.Piano())

    is_first_measure = True
    for measure_data in part_data["measures"]:
        right_measure = stream.Measure(number=measure_data["number"])
        left_measure = stream.Measure(number=measure_data["number"])
        if is_first_measure:
            # Verified directly: the tempo mark only needs to go on ONE
            # staff's first measure (the right/treble one, by convention)
            # — it renders once in the output, not duplicated per staff.
            right_measure.insert(0, tempo.MetronomeMark(number=part_data["tempoBpm"]))
            is_first_measure = False
        for event in measure_data["events"]:
            n = note.Note(_spell_pitch(event["pitch"], key_obj))
            n.duration = duration.Duration(_notated_fraction_to_quarter_length(event["notatedDuration"]))
            target = right_measure if _hand_for_event(event) == "right" else left_measure
            # insert-at-onset, not append — see _build_single_staff.
            target.insert(_notated_fraction_to_quarter_length(event["notatedOnset"]), n)
        right.append(right_measure)
        left.append(left_measure)

    # Verified directly: PartStaff (not plain Part) + StaffGroup with
    # symbol="brace" merges into ONE <part> with <staves>2</staves>, correct
    # per-staff clefs, and music21 fills a full-measure rest automatically
    # for any measure where one hand has no notes — no manual rest needed.
    staff_group = layout.StaffGroup([right, left], name="Piano", symbol="brace")
    m21_score = stream.Score()
    m21_score.insert(0, right)
    m21_score.insert(0, left)
    m21_score.insert(0, staff_group)
    return m21_score
