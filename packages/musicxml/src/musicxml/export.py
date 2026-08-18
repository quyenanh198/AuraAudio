# packages/musicxml/src/musicxml/export.py — full replacement
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from music21 import articulations, chord, clef, duration, instrument, key as m21_key, layout, meter as m21_meter, note, pitch as m21_pitch, stream, tempo

# packages/musicxml cannot depend on workers/transcription (packages sit
# below workers in the dependency graph) — duplicated here rather than
# imported from aura_worker.piano_hands, same reasoning as this file's
# inlined "6 - internal_string" guitar-numbering conversion.
_STANDARD_PIANO_RANGE = (21, 108)


def _notated_fraction_to_quarter_length(value: str) -> float:
    """A notated value like '1/4' means one quarter of a whole note (a
    quarter note = 1.0 quarterLength in music21), so quarterLength = 4 * fraction."""
    return float(Fraction(value) * 4)


def _measure_length_ql(meter_str: str) -> float:
    """Total quarterLength of one measure for a "N/D" meter string, e.g.
    '4/4' -> 4.0, '3/4' -> 3.0, '6/8' -> 3.0."""
    numerator_str, denominator_str = meter_str.split("/")
    return int(numerator_str) * (4.0 / int(denominator_str))


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


def _build_note(event: dict, key_obj: m21_key.Key, with_technical: bool) -> note.Note:
    n = note.Note(_spell_pitch(event["pitch"], key_obj))
    n.duration = duration.Duration(_notated_fraction_to_quarter_length(event["notatedDuration"]))
    if with_technical:
        internal_string = event.get("string")
        fret = event.get("fret")
        if internal_string is not None and fret is not None:
            musicxml_string = 6 - internal_string
            n.articulations.append(articulations.StringIndication(musicxml_string))
            n.articulations.append(articulations.FretIndication(fret))
    return n


def _events_to_notes_or_chords(
    events: list[dict], key_obj: m21_key.Key, with_technical: bool
) -> list[tuple[float, note.GeneralNote]]:
    """R3: group events sharing a notatedOnset into a single chord; a group
    of one stays a plain Note. Grouping key is notatedOnset only — members
    may carry different offsetSeconds in pathological data. Returns
    (offset_quarterLength, element) pairs sorted by offset, ready to be
    placed with measure.insert()."""
    groups: dict[Fraction, list[dict]] = {}
    for event in events:
        groups.setdefault(Fraction(event["notatedOnset"]), []).append(event)

    result: list[tuple[float, note.GeneralNote]] = []
    for onset_fraction in sorted(groups):
        group = groups[onset_fraction]
        offset_ql = float(onset_fraction * 4)
        member_notes = [_build_note(event, key_obj, with_technical) for event in group]

        if len(member_notes) == 1:
            element: note.GeneralNote = member_notes[0]
        else:
            element = chord.Chord(member_notes)
            # Chord duration: members should share notatedDuration; music21
            # derives the Chord's duration from its first constituent Note,
            # which already matches the documented fallback ("if members
            # disagree, use the first event's duration") — set explicitly
            # here so that fallback is not an implicit accident.
            element.duration = duration.Duration(member_notes[0].duration.quarterLength)
            if with_technical:
                # Investigated: music21's MusicXML writer (m21ToXml.py,
                # noteToNotations) reads StringIndication/FretIndication
                # only from the Chord object's OWN .articulations list, and
                # applies them solely to the chord's first <note> — with
                # StringIndication/FretIndication being ordinary
                # TechnicalIndication (not Fingering), there is no
                # noteIndexInChord-matched per-member slot the way there is
                # for Fingering marks. Articulations attached to each
                # member Note before chord construction (n.articulations)
                # are preserved on chord.notes[i] but are never read at
                # export time. Concatenating every member's marks onto the
                # chord's own .articulations is therefore the only way to
                # surface all fret/string values in the exported file (they
                # land together on the first <note>'s <technical> block,
                # not split across each pitch's own <note> — an accepted
                # limitation, documented in the report).
                combined_articulations: list[articulations.Articulation] = []
                for member in member_notes:
                    combined_articulations.extend(member.articulations)
                element.articulations = combined_articulations

        result.append((offset_ql, element))
    return result


def _insert_notated_events(
    m21_measure: stream.Measure,
    elements: list[tuple[float, note.GeneralNote]],
    measure_length_ql: float,
) -> None:
    """R2: place every note/chord at its true offset within the measure,
    explicitly filling any gap before, between, or after them with rests.

    music21's own gap-filler (Measure.makeRests(fillGaps=True)) resolves the
    measure's bar length via a context search across the Part/Measure
    hierarchy that is unreliable when called on a bare Measure outside a
    Score (it silently falls back to guessing the bar length from whatever
    content is already in the measure, under- or over-filling gaps) —
    verified directly while implementing this. Filling gaps by hand sidesteps
    that entirely and is simple to reason about.
    """
    cursor = 0.0
    for offset_ql, element in elements:
        if offset_ql > cursor:
            m21_measure.insert(cursor, note.Rest(quarterLength=offset_ql - cursor))
        m21_measure.insert(offset_ql, element)
        cursor = offset_ql + element.duration.quarterLength
    if cursor < measure_length_ql:
        m21_measure.insert(cursor, note.Rest(quarterLength=measure_length_ql - cursor))


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
    # R1: guitar gets a standard notation staff plus a linked TAB staff.
    # _build_single_staff is also the fallback for any non-guitar,
    # non-piano instrument value (piano always routes to the grand-staff
    # builder in score_json_to_musicxml) — that fallback stays single-staff.
    if part_data["instrument"] == "guitar":
        return _build_guitar_notation_and_tab(part_data, key_obj)

    m21_part = stream.Part()
    m21_part.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    m21_part.insert(0, key_obj)
    m21_part.insert(0, instrument.Piano())

    measure_length_ql = _measure_length_ql(part_data["meter"])
    is_first_measure = True
    for measure_data in part_data["measures"]:
        m21_measure = stream.Measure(number=measure_data["number"])
        if is_first_measure:
            # MetronomeMark must live in the Measure, not the Part — see the
            # "Validated design notes" in the implementation plan: inserting
            # it at the Part level silently drops it from the exported XML.
            m21_measure.insert(0, tempo.MetronomeMark(number=part_data["tempoBpm"]))
            is_first_measure = False
        elements = _events_to_notes_or_chords(measure_data["events"], key_obj, with_technical=False)
        _insert_notated_events(m21_measure, elements, measure_length_ql)
        m21_part.append(m21_measure)

    m21_score = stream.Score()
    m21_score.insert(0, m21_part)
    return m21_score


def _build_guitar_notation_and_tab(part_data: dict, key_obj: m21_key.Key) -> stream.Score:
    """R1: two linked staves for guitar — standard notation (staff 1) and a
    TAB staff (staff 2, clef.TabClef() at offset 0) carrying the same notes
    plus the StringIndication/FretIndication articulations. Built with
    stream.PartStaff + layout.StaffGroup, mirroring
    _build_piano_grand_staff's pattern — verified directly that PartStaff
    merging into one <part> with <staves>2</staves> works correctly even
    with mismatched clef types (treble vs TAB) across the two staves:
    correct per-staff clefs, fret data intact on reopen. No need to fall
    back to two plain Parts."""
    notation = stream.PartStaff()
    notation.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    notation.insert(0, key_obj)
    notation.insert(0, clef.TrebleClef())
    notation.insert(0, instrument.Guitar())

    tab = stream.PartStaff()
    tab.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    tab.insert(0, key_obj)
    tab.insert(0, clef.TabClef())
    tab.insert(0, instrument.Guitar())

    measure_length_ql = _measure_length_ql(part_data["meter"])
    is_first_measure = True
    for measure_data in part_data["measures"]:
        notation_measure = stream.Measure(number=measure_data["number"])
        tab_measure = stream.Measure(number=measure_data["number"])
        if is_first_measure:
            # Tempo only needs to render on one staff (the notation staff,
            # by convention) — same verified behavior as the piano builder.
            notation_measure.insert(0, tempo.MetronomeMark(number=part_data["tempoBpm"]))
            is_first_measure = False

        notation_elements = _events_to_notes_or_chords(measure_data["events"], key_obj, with_technical=False)
        tab_elements = _events_to_notes_or_chords(measure_data["events"], key_obj, with_technical=True)
        _insert_notated_events(notation_measure, notation_elements, measure_length_ql)
        _insert_notated_events(tab_measure, tab_elements, measure_length_ql)

        notation.append(notation_measure)
        tab.append(tab_measure)

    staff_group = layout.StaffGroup([notation, tab], name="Guitar", symbol="bracket")
    m21_score = stream.Score()
    m21_score.insert(0, notation)
    m21_score.insert(0, tab)
    m21_score.insert(0, staff_group)
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

    measure_length_ql = _measure_length_ql(part_data["meter"])
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

        right_events = [e for e in measure_data["events"] if _hand_for_event(e) == "right"]
        left_events = [e for e in measure_data["events"] if _hand_for_event(e) == "left"]
        right_elements = _events_to_notes_or_chords(right_events, key_obj, with_technical=False)
        left_elements = _events_to_notes_or_chords(left_events, key_obj, with_technical=False)
        _insert_notated_events(right_measure, right_elements, measure_length_ql)
        _insert_notated_events(left_measure, left_elements, measure_length_ql)

        right.append(right_measure)
        left.append(left_measure)

    # Verified directly: PartStaff (not plain Part) + StaffGroup with
    # symbol="brace" merges into ONE <part> with <staves>2</staves>, correct
    # per-staff clefs, and explicit rests (inserted above by
    # _insert_notated_events) fill any measure where one hand has no notes.
    staff_group = layout.StaffGroup([right, left], name="Piano", symbol="brace")
    m21_score = stream.Score()
    m21_score.insert(0, right)
    m21_score.insert(0, left)
    m21_score.insert(0, staff_group)
    return m21_score
