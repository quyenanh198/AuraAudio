from pathlib import Path

import music21
from score_schema.models import build_score

from musicxml.export import score_json_to_musicxml


def _sample_score(meter: str = "4/4", key: str = "C major", tempo_bpm: float = 120.0):
    return build_score(
        instrument="guitar",
        tempo_bpm=tempo_bpm,
        meter=meter,
        key=key,
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.9, "locked": False,
                    },
                    {
                        "id": "note_01", "pitch": 67, "onsetSeconds": 0.5, "offsetSeconds": 1.0,
                        "notatedOnset": "1/4", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.85, "locked": False,
                    },
                ],
            }
        ],
    )


def test_score_json_to_musicxml_writes_a_file(tmp_path: Path):
    out_path = tmp_path / "out.musicxml"
    result_path = score_json_to_musicxml(_sample_score(), out_path)

    assert result_path == out_path
    assert out_path.exists()
    content = out_path.read_text()
    assert "<score-partwise" in content
    assert content.count("<note>") == 2


def test_score_json_to_musicxml_uses_detected_time_signature(tmp_path: Path):
    out_path = tmp_path / "out34.musicxml"
    score_json_to_musicxml(_sample_score(meter="3/4"), out_path)
    content = out_path.read_text()
    assert "<beats>3</beats>" in content
    assert "<beat-type>4</beat-type>" in content


def test_score_json_to_musicxml_uses_detected_tempo(tmp_path: Path):
    out_path = tmp_path / "out_tempo.musicxml"
    score_json_to_musicxml(_sample_score(tempo_bpm=90.0), out_path)
    content = out_path.read_text()
    assert "<per-minute>90</per-minute>" in content
    assert 'sound tempo="90"' in content


def test_score_json_to_musicxml_uses_detected_key(tmp_path: Path):
    out_path = tmp_path / "out_key.musicxml"
    score_json_to_musicxml(_sample_score(key="D major"), out_path)
    content = out_path.read_text()
    assert "<fifths>2</fifths>" in content
    assert "<mode>major</mode>" in content


def test_score_json_to_musicxml_spells_pitch_using_key_context(tmp_path: Path):
    # Pitch 66 (F#/Gb) is diatonic in D major as F#, but NOT diatonic in F
    # major (which uses flats) — so the same MIDI pitch should spell
    # differently depending on the score's detected key.
    score_sharp_key = _sample_score(key="D major")
    score_sharp_key["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 66, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path = tmp_path / "sharp.musicxml"
    score_json_to_musicxml(score_sharp_key, out_path)
    assert "<step>F</step>" in out_path.read_text()

    score_flat_key = _sample_score(key="F major")
    score_flat_key["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 66, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path2 = tmp_path / "flat.musicxml"
    score_json_to_musicxml(score_flat_key, out_path2)
    assert "<step>G</step>" in out_path2.read_text()


def test_score_json_to_musicxml_spells_bsharp_leading_tone_at_correct_octave(tmp_path: Path):
    # In C# major, MIDI 60 (C4) is diatonic as B# (the leading tone), which
    # sounds a semitone below C4 — i.e. B#3, not B#4. Round-tripping the
    # exported file through music21 must recover MIDI 60, not 72.
    score = _sample_score(key="C# major")
    score["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path = tmp_path / "bsharp.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<step>B</step>" in content
    assert "<alter>1</alter>" in content
    assert "<octave>3</octave>" in content

    reopened = music21.converter.parse(str(out_path))
    reopened_notes = list(reopened.flatten().notes)
    assert len(reopened_notes) == 1
    assert reopened_notes[0].pitch.midi == 60


def test_score_json_to_musicxml_spells_cflat_at_correct_octave(tmp_path: Path):
    # In E- minor, MIDI 71 (B4) is diatonic as C- (Cb), the flat second
    # degree of the harmonic-minor-derived collection, sounding as B4 —
    # i.e. C-5, not C-4. Round-tripping must recover MIDI 71, not 59.
    score = _sample_score(key="E- minor")
    score["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 71, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path = tmp_path / "cflat.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<step>C</step>" in content
    assert "<alter>-1</alter>" in content
    assert "<octave>5</octave>" in content

    reopened = music21.converter.parse(str(out_path))
    reopened_notes = list(reopened.flatten().notes)
    assert len(reopened_notes) == 1
    assert reopened_notes[0].pitch.midi == 71


def test_score_json_to_musicxml_renders_string_and_fret(tmp_path: Path):
    score = _sample_score()
    # internal string=2 (low-to-high, 0-indexed) -> MusicXML string 6-2=4
    score["parts"][0]["measures"][0]["events"][0]["string"] = 2
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 5
    out_path = tmp_path / "tab.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<string>4</string>" in content
    assert "<fret>5</fret>" in content


def test_score_json_to_musicxml_omits_technical_block_when_unassigned(tmp_path: Path):
    score = _sample_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = None
    score["parts"][0]["measures"][0]["events"][0]["fret"] = None
    out_path = tmp_path / "no_tab.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<technical>" not in content


def test_score_json_to_musicxml_omits_technical_block_when_keys_absent(tmp_path: Path):
    # A score built without ever running the assign stage (e.g. a piano
    # score, or a guitar score from before assign ran) has no string/fret
    # keys on its events at all — export must not crash on the missing keys.
    score = _sample_score()
    out_path = tmp_path / "no_keys.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<technical>" not in content


def _piano_score(events_by_measure: list[list[dict]]):
    return build_score(
        instrument="piano",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[
            {"number": i + 1, "events": events}
            for i, events in enumerate(events_by_measure)
        ],
    )


def _piano_event(id_: str, pitch: int, hand, onset: str) -> dict:
    return {
        "id": id_, "pitch": pitch, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": onset, "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False, "hand": hand,
    }


def test_score_json_to_musicxml_renders_piano_grand_staff(tmp_path: Path):
    score = _piano_score([[
        _piano_event("note_00", 40, "left", "0/1"),
        _piano_event("note_01", 76, "right", "1/4"),
    ]])
    out_path = tmp_path / "piano.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()

    assert "<staves>2</staves>" in content
    # verified directly: treble (right hand) is clef number 1 (G clef),
    # bass (left hand) is clef number 2 (F clef)
    assert content.index('<clef number="1">') < content.index('<clef number="2">')
    assert "<sign>G</sign>" in content
    assert "<sign>F</sign>" in content

    reopened = music21.converter.parse(str(out_path))
    reopened_notes = list(reopened.flatten().notes)
    assert len(reopened_notes) == 2
    assert {n.pitch.midi for n in reopened_notes} == {40, 76}

    # Pin each note to its *specific* staff, not just "staff 2 appears
    # somewhere" — reopening splits the merged part back into two
    # PartStaff objects, one per staff, named "...-Staff1"/"...-Staff2".
    # This distinguishes "hand assignment worked" from "hand assignment
    # was disabled" (which would push everything onto staff 1/right).
    notes_by_staff = {
        part.id.rsplit("-", 1)[-1]: {n.pitch.midi for n in part.recurse().notes}
        for part in reopened.parts
    }
    assert notes_by_staff["Staff1"] == {76}  # right hand -> treble/staff 1
    assert notes_by_staff["Staff2"] == {40}  # left hand -> bass/staff 2


def test_score_json_to_musicxml_piano_out_of_range_note_still_renders(tmp_path: Path):
    # A note with hand: null (out of STANDARD_PIANO_RANGE) must still
    # appear in the file, clamped to the nearer staff — never silently
    # dropped, per the spec's explicit rule.
    score = _piano_score([[
        _piano_event("note_00", 10, None, "0/1"),  # below range -> clamps to left/bass
        _piano_event("note_01", 76, "right", "1/4"),
    ]])
    out_path = tmp_path / "piano_clamp.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()

    reopened = music21.converter.parse(str(out_path))
    reopened_notes = list(reopened.flatten().notes)
    assert len(reopened_notes) == 2  # not dropped
    assert {n.pitch.midi for n in reopened_notes} == {10, 76}

    # Not just "both notes exist somewhere" — the clamped out-of-range note
    # (pitch 10) must specifically land on staff 2/bass, and the in-range
    # right-hand note stays on staff 1/treble.
    notes_by_staff = {
        part.id.rsplit("-", 1)[-1]: {n.pitch.midi for n in part.recurse().notes}
        for part in reopened.parts
    }
    assert notes_by_staff["Staff1"] == {76}
    assert notes_by_staff["Staff2"] == {10}


def test_score_json_to_musicxml_guitar_export_unaffected_by_piano_branch(tmp_path: Path):
    # Regression check: guitar's single-staff path must still produce
    # exactly one <part> with no <staves> element at all.
    out_path = tmp_path / "guitar_regression.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    content = out_path.read_text()
    assert "<staves>" not in content
    assert content.count("<part ") == 1


def _guitar_score_with_events(events: list[dict]):
    return build_score(
        instrument="guitar",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        measures=[{"number": 1, "events": events}],
    )


def _guitar_event(id_: str, pitch: int, onset: str, dur: str = "1/4") -> dict:
    return {
        "id": id_, "pitch": pitch, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": onset, "notatedDuration": dur, "voice": 1,
        "confidence": 0.9, "locked": False,
    }


def test_score_json_to_musicxml_places_notes_at_their_notated_onset(tmp_path: Path):
    # An event list is not guaranteed to arrive sorted by notatedOnset —
    # real inference output isn't — so the exporter must place each note at
    # its own notated onset rather than packing notes back-to-back in list
    # order. Deliberately fed in descending-onset order; distinct pitches
    # let us pin exactly which note landed where.
    score = _guitar_score_with_events([
        _guitar_event("note_00", 72, "3/4"),  # beat 4
        _guitar_event("note_01", 60, "0/1"),  # beat 1
        _guitar_event("note_02", 64, "1/2"),  # beat 3
    ])
    out_path = tmp_path / "unsorted.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    measure = reopened.parts[0].getElementsByClass(music21.stream.Measure)[0]
    placed = {n.pitch.midi: float(n.offset) for n in measure.notes}
    assert placed == {60: 0.0, 64: 2.0, 72: 3.0}


def test_score_json_to_musicxml_leaves_a_rest_in_a_gap(tmp_path: Path):
    # Packing notes back-to-back also swallowed rests: a gap between two
    # notated onsets must survive export as an actual rest, not close up.
    score = _guitar_score_with_events([
        _guitar_event("note_00", 60, "0/1"),  # beat 1, quarter
        _guitar_event("note_01", 67, "1/2"),  # beat 3, quarter -> beat 2 is silent
    ])
    out_path = tmp_path / "gap.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    measure = reopened.parts[0].getElementsByClass(music21.stream.Measure)[0]
    rests = list(measure.getElementsByClass(music21.note.Rest))
    assert [(float(r.offset), float(r.quarterLength)) for r in rests] == [(1.0, 1.0)]
    assert {n.pitch.midi: float(n.offset) for n in measure.notes} == {60: 0.0, 67: 2.0}


def test_score_json_to_musicxml_piano_places_notes_at_their_notated_onset(tmp_path: Path):
    # Same fix, per hand: the grand-staff path built each staff's measure by
    # appending too, so an unsorted event list scrambled both staves.
    score = _piano_score([[
        _piano_event("note_00", 79, "right", "1/2"),  # beat 3, treble
        _piano_event("note_01", 76, "right", "0/1"),  # beat 1, treble
        _piano_event("note_02", 40, "left", "1/4"),   # beat 2, bass
    ]])
    out_path = tmp_path / "piano_unsorted.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    placed = {
        n.pitch.midi: float(n.getOffsetInHierarchy(reopened))
        for n in reopened.recurse().notes
    }
    assert placed == {76: 0.0, 40: 1.0, 79: 2.0}
