import re
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
    # Guitar now exports a linked TAB staff (R1) that mirrors every note
    # from the notation staff, so the 2 sample events (2.0 quarterLengths
    # total, in a 4/4 measure) produce 4 real <note> elements (2 per staff)
    # plus a trailing half-rest per staff filling the rest of the measure
    # (R2) — 6 <note> elements total (rests are <note><rest/>...</note>).
    assert content.count("<note>") == 6


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


def test_score_json_to_musicxml_rounds_raw_float_tempo_to_integer_mm(tmp_path: Path):
    # Bug D cosmetic fix: a real librosa.beat.beat_track result (e.g.
    # 99.38401442307692, see aura_worker.stages.structure) must be rounded
    # for the notated tempo MARK -- printed scores conventionally notate a
    # plain integer MM, and the raw float rendered directly was the
    # reported bug. The precise value itself is a separate JSON-only
    # concern, not exercised by this MusicXML-only test.
    out_path = tmp_path / "out_raw_tempo.musicxml"
    score_json_to_musicxml(_sample_score(tempo_bpm=99.38401442307692), out_path)
    content = out_path.read_text()
    assert "<per-minute>99</per-minute>" in content
    assert 'sound tempo="99"' in content
    assert "99.38401442307692" not in content


def test_score_json_to_musicxml_rounds_tempo_half_up_at_the_boundary(tmp_path: Path):
    # round() in Python uses banker's rounding (round-half-to-even) --
    # confirms the exact behavior this fix inherits rather than assuming
    # ordinary round-half-up, so a future reader isn't surprised by
    # e.g. 120.5 -> 120, not 121.
    out_path = tmp_path / "out_half_tempo.musicxml"
    score_json_to_musicxml(_sample_score(tempo_bpm=120.5), out_path)
    content = out_path.read_text()
    assert "<per-minute>120</per-minute>" in content


def test_score_json_to_musicxml_piano_tempo_mark_is_also_rounded(tmp_path: Path):
    # The piano grand-staff builder has its own separate MetronomeMark call
    # site (musicxml.export._build_piano_grand_staff) -- must not regress
    # independently of the guitar/single-staff one covered above.
    score = build_score(
        instrument="piano",
        tempo_bpm=143.87654321,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [
                {
                    "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.9, "locked": False, "hand": "right",
                },
            ],
        }],
    )
    out_path = tmp_path / "out_piano_tempo.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<per-minute>144</per-minute>" in content
    assert "143.87654321" not in content


class TestTitleMetadata:
    """Bug D cosmetic fix: the exported score's title/composer must never
    show music21's own hardcoded "Music21 Fragment"/"Music21" placeholders
    (music21/defaults.py's `title`/`author`, written into
    <movement-title>/<work-title>/<creator> by m21ToXml.py's
    setIdentification whenever no metadata/contributor is set at all --
    verified directly, see _apply_metadata's doc comment in export.py)."""

    def test_real_project_title_is_used(self, tmp_path: Path):
        out_path = tmp_path / "out_title.musicxml"
        score_json_to_musicxml(_sample_score(), out_path, title="Fairy Tale")
        content = out_path.read_text()
        assert "<movement-title>Fairy Tale</movement-title>" in content
        assert "<work-title>Fairy Tale</work-title>" in content
        assert "Music21" not in content

    def test_no_title_falls_back_to_untitled_not_music21(self, tmp_path: Path):
        out_path = tmp_path / "out_no_title.musicxml"
        score_json_to_musicxml(_sample_score(), out_path)  # title omitted entirely
        content = out_path.read_text()
        assert "<movement-title>Untitled</movement-title>" in content
        assert "Music21 Fragment" not in content
        assert "Music21" not in content

    def test_empty_string_title_falls_back_to_untitled(self, tmp_path: Path):
        # An empty-string project title is falsy in Python -- must be
        # treated the same as "no title given", not written as a literal
        # blank <movement-title></movement-title>.
        out_path = tmp_path / "out_empty_title.musicxml"
        score_json_to_musicxml(_sample_score(), out_path, title="")
        content = out_path.read_text()
        assert "<movement-title>Untitled</movement-title>" in content

    def test_composer_is_blank_not_music21(self, tmp_path: Path):
        out_path = tmp_path / "out_composer.musicxml"
        score_json_to_musicxml(_sample_score(), out_path, title="Fairy Tale")
        content = out_path.read_text()
        assert '<creator type="composer" />' in content or '<creator type="composer"/>' in content
        assert "<creator type=\"composer\">Music21</creator>" not in content

    def test_piano_export_also_gets_real_title(self, tmp_path: Path):
        score = build_score(
            instrument="piano",
            tempo_bpm=120.0,
            meter="4/4",
            key="C major",
            confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
            time_map=[{"beat": 0, "seconds": 0.0}],
            measures=[{
                "number": 1,
                "events": [
                    {
                        "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.9, "locked": False, "hand": "right",
                    },
                ],
            }],
        )
        out_path = tmp_path / "out_piano_title.musicxml"
        score_json_to_musicxml(score, out_path, title="Fairy Tale")
        content = out_path.read_text()
        assert "<movement-title>Fairy Tale</movement-title>" in content
        assert "Music21" not in content


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

    # Guitar now has a linked TAB staff mirroring every note (R1), so filter
    # to the notation staff (Staff1) — the same single logical note, not its
    # TAB-staff duplicate.
    reopened = music21.converter.parse(str(out_path))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    reopened_notes = list(notation_part.recurse().notes)
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

    # Guitar now has a linked TAB staff mirroring every note (R1), so filter
    # to the notation staff (Staff1) — the same single logical note, not its
    # TAB-staff duplicate.
    reopened = music21.converter.parse(str(out_path))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    reopened_notes = list(notation_part.recurse().notes)
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
    # Regression check: guitar's staff-building path must not accidentally
    # go through the piano grand-staff builder. Guitar now legitimately has
    # its own two staves (R1: notation + TAB), merged into a single <part>
    # via PartStaff — same merge mechanism as piano's grand staff, so
    # <staves>2</staves> is expected here too, but it must stay ONE <part>
    # (not the piano-specific bass/treble clef pairing).
    out_path = tmp_path / "guitar_regression.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    content = out_path.read_text()
    assert "<staves>2</staves>" in content
    assert content.count("<part ") == 1
    assert "<sign>F</sign>" not in content  # no piano bass clef leaked in


# --- R1: guitar TAB staff -------------------------------------------------


def test_score_json_to_musicxml_guitar_has_tab_clef_and_two_staves(tmp_path: Path):
    out_path = tmp_path / "guitar_tab_clef.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    content = out_path.read_text()
    assert "<sign>TAB</sign>" in content
    assert "<staves>2</staves>" in content


def test_score_json_to_musicxml_guitar_fret_data_sits_on_tab_staff(tmp_path: Path):
    score = _sample_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = 2
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 5
    out_path = tmp_path / "guitar_tab_fret.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<fret>5</fret>" in content

    reopened = music21.converter.parse(str(out_path))
    tab_part = next(p for p in reopened.parts if p.id.endswith("Staff2"))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    tab_frets = [
        art for n in tab_part.recurse().notes for art in n.articulations
        if isinstance(art, music21.articulations.FretIndication)
    ]
    notation_frets = [
        art for n in notation_part.recurse().notes for art in n.articulations
        if isinstance(art, music21.articulations.FretIndication)
    ]
    assert any(f.number == 5 for f in tab_frets)
    assert notation_frets == []  # notation staff keeps current (no-fret) behavior


# --- R3: same-onset events become a chord ---------------------------------


def test_score_json_to_musicxml_same_onset_guitar_events_become_chord(tmp_path: Path):
    score = _sample_score()
    score["parts"][0]["measures"][0]["events"] = [
        {
            "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
            "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
            "confidence": 0.9, "locked": False, "string": 2, "fret": 2,
        },
        {
            "id": "note_01", "pitch": 67, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
            "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
            "confidence": 0.85, "locked": False, "string": 3, "fret": 0,
        },
    ]
    out_path = tmp_path / "guitar_chord.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert re.search(r"<chord\s*/>", content)
    assert "<fret>2</fret>" in content
    assert "<fret>0</fret>" in content


# --- R2: onset-faithful placement ------------------------------------------


def test_score_json_to_musicxml_piano_right_hand_lands_at_true_onset(tmp_path: Path):
    # Left hand at onset 0/1 (half note); right hand at onset 1/2 (half
    # note) — they must NOT both land at beat 0.
    score = _piano_score([[
        {
            "id": "l0", "pitch": 40, "onsetSeconds": 0.0, "offsetSeconds": 1.0,
            "notatedOnset": "0/1", "notatedDuration": "1/2", "voice": 1,
            "confidence": 0.9, "locked": False, "hand": "left",
        },
        {
            "id": "r0", "pitch": 76, "onsetSeconds": 1.0, "offsetSeconds": 2.0,
            "notatedOnset": "1/2", "notatedDuration": "1/2", "voice": 1,
            "confidence": 0.9, "locked": False, "hand": "right",
        },
    ]])
    out_path = tmp_path / "piano_onset.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    right_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    right_notes = list(right_part.recurse().notes)
    assert len(right_notes) == 1
    assert right_notes[0].offset == 2.0  # not 0.0

    right_rests = list(right_part.recurse().getElementsByClass(music21.note.Rest))
    assert any(r.duration.quarterLength == 2.0 for r in right_rests)  # half rest precedes it


def test_score_json_to_musicxml_guitar_gap_between_onsets_becomes_rest(tmp_path: Path):
    # Events at 0/1 (dur 1/4) and 1/2 (dur 1/4) leave a gap at 1/4-1/2 that
    # must be filled with a rest, not silently skipped.
    score = _sample_score()
    score["parts"][0]["measures"][0]["events"] = [
        {
            "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
            "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
            "confidence": 0.9, "locked": False,
        },
        {
            "id": "note_01", "pitch": 67, "onsetSeconds": 1.0, "offsetSeconds": 1.5,
            "notatedOnset": "1/2", "notatedDuration": "1/4", "voice": 1,
            "confidence": 0.85, "locked": False,
        },
    ]
    out_path = tmp_path / "guitar_gap.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    notes = list(notation_part.recurse().notes)
    assert len(notes) == 2
    assert notes[1].offset == 2.0  # 1/2 whole note = 2.0 quarterLengths

    rests = list(notation_part.recurse().getElementsByClass(music21.note.Rest))
    assert any(r.duration.quarterLength == 1.0 for r in rests)  # the 1/4-1/2 gap


# --- CRITICAL 1: clamp overlapping/bar-crossing durations ------------------


def test_score_json_to_musicxml_clamps_intra_measure_overlap(tmp_path: Path):
    # note at 0/1 dur 2 beats (half note) overlaps note at 1/4 dur 1 beat
    # (quarter note at beat 1) in 4/4 — the first note's notated duration
    # must be clamped down to the room before the second note's onset, the
    # second note must stay exactly at beat 1, and the measure must come out
    # to exactly 4 quarterLengths (no over-full measure).
    score = _sample_score()
    score["parts"][0]["measures"][0]["events"] = [
        {
            "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 1.0,
            "notatedOnset": "0/1", "notatedDuration": "1/2", "voice": 1,
            "confidence": 0.9, "locked": False,
        },
        {
            "id": "note_01", "pitch": 67, "onsetSeconds": 0.5, "offsetSeconds": 1.0,
            "notatedOnset": "1/4", "notatedDuration": "1/4", "voice": 1,
            "confidence": 0.85, "locked": False,
        },
    ]
    out_path = tmp_path / "guitar_overlap.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    notes = list(notation_part.recurse().notes)
    assert len(notes) == 2
    assert notes[0].offset == 0.0
    assert notes[0].duration.quarterLength == 1.0  # clamped from 2.0 down to the room before beat 1
    assert notes[1].offset == 1.0  # second note stays at beat 1, not displaced
    assert notes[1].duration.quarterLength == 1.0  # unchanged, it already fit

    measure = notation_part.recurse().getElementsByClass(music21.stream.Measure)[0]
    assert measure.duration.quarterLength == 4.0  # exactly one 4/4 bar, not over-full


# --- Silent-measure fidelity: a zero-event measure renders as one
# whole-measure rest on every staff -----------------------------------------


def test_score_json_to_musicxml_guitar_empty_measure_is_whole_bar_rest_four_four(tmp_path: Path):
    # Middle measure (2) is a silent measure (quantize.py's silent-measure
    # fidelity fix emits it as {"number": 2, "events": []}) between two
    # measures that do have notes. Both notation and TAB staves must render
    # it as exactly one whole-measure rest, not shrink/omit the bar.
    score = _sample_score()
    score["parts"][0]["measures"] = [
        {"number": 1, "events": score["parts"][0]["measures"][0]["events"]},
        {"number": 2, "events": []},
        {
            "number": 3,
            "events": [
                {
                    "id": "note_02", "pitch": 62, "onsetSeconds": 2.0, "offsetSeconds": 2.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
            ],
        },
    ]
    out_path = tmp_path / "guitar_silent_measure_44.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    for suffix in ("Staff1", "Staff2"):  # notation and TAB
        part = next(p for p in reopened.parts if p.id.endswith(suffix))
        measures = part.recurse().getElementsByClass(music21.stream.Measure)
        assert len(measures) == 3
        silent_measure = measures[1]
        assert silent_measure.number == 2
        assert silent_measure.duration.quarterLength == 4.0  # exactly one 4/4 bar
        notes_in_silent_measure = list(silent_measure.recurse().notes)
        assert notes_in_silent_measure == []
        rests_in_silent_measure = list(silent_measure.recurse().getElementsByClass(music21.note.Rest))
        assert len(rests_in_silent_measure) == 1
        assert rests_in_silent_measure[0].duration.quarterLength == 4.0


def test_score_json_to_musicxml_guitar_empty_measure_is_whole_bar_rest_three_four(tmp_path: Path):
    # Same as above, but in 3/4 — the whole-bar rest must be 3.0
    # quarterLengths, not the 4/4 default.
    score = _sample_score(meter="3/4")
    score["parts"][0]["measures"] = [
        {"number": 1, "events": []},
        {"number": 2, "events": score["parts"][0]["measures"][0]["events"]},
    ]
    out_path = tmp_path / "guitar_silent_measure_34.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    measures = notation_part.recurse().getElementsByClass(music21.stream.Measure)
    silent_measure = measures[0]
    assert silent_measure.number == 1
    assert silent_measure.duration.quarterLength == 3.0  # exactly one 3/4 bar
    rests_in_silent_measure = list(silent_measure.recurse().getElementsByClass(music21.note.Rest))
    assert len(rests_in_silent_measure) == 1
    assert rests_in_silent_measure[0].duration.quarterLength == 3.0


def test_score_json_to_musicxml_piano_empty_measure_is_whole_bar_rest_on_both_hands(tmp_path: Path):
    # A silent measure on a piano part must render a whole-bar rest on
    # BOTH the right (treble) and left (bass) staves.
    score = _piano_score([
        [_piano_event("note_00", 60, "right", "0/1")],
        [],
    ])
    out_path = tmp_path / "piano_silent_measure.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    for suffix in ("Staff1", "Staff2"):  # right/treble, left/bass
        part = next(p for p in reopened.parts if p.id.endswith(suffix))
        measures = part.recurse().getElementsByClass(music21.stream.Measure)
        assert len(measures) == 2
        silent_measure = measures[1]
        assert silent_measure.number == 2
        assert silent_measure.duration.quarterLength == 4.0
        assert list(silent_measure.recurse().notes) == []
        rests = list(silent_measure.recurse().getElementsByClass(music21.note.Rest))
        assert len(rests) == 1
        assert rests[0].duration.quarterLength == 4.0


def test_score_json_to_musicxml_clamps_bar_crossing_duration(tmp_path: Path):
    # A note at beat 3 with a notated duration of 2 beats would run past the
    # 4/4 measure's end (beat 4) into the next measure. It must be clamped
    # to end exactly at the bar line instead of overrunning into a tied
    # continuation note that has no corresponding onset group in the score
    # JSON. The next measure's own note must still land at its own true
    # onset (beat 0 of measure 2), and both measures must be exactly 4
    # quarterLengths long.
    score = _sample_score()
    score["parts"][0]["measures"] = [
        {
            "number": 1,
            "events": [
                {
                    "id": "note_00", "pitch": 64, "onsetSeconds": 1.5, "offsetSeconds": 2.5,
                    "notatedOnset": "3/4", "notatedDuration": "1/2", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
            ],
        },
        {
            "number": 2,
            "events": [
                {
                    "id": "note_01", "pitch": 67, "onsetSeconds": 2.0, "offsetSeconds": 2.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.85, "locked": False,
                },
            ],
        },
    ]
    out_path = tmp_path / "guitar_barcross.musicxml"
    score_json_to_musicxml(score, out_path)

    reopened = music21.converter.parse(str(out_path))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    measures = notation_part.recurse().getElementsByClass(music21.stream.Measure)
    assert len(measures) == 2
    assert measures[0].duration.quarterLength == 4.0
    assert measures[1].duration.quarterLength == 4.0

    notes = list(notation_part.recurse().notes)
    assert len(notes) == 2  # no stray tied-continuation note with no JSON onset
    assert notes[0].offset == 3.0  # measure 1, beat 3 (bar-relative)
    assert notes[0].duration.quarterLength == 1.0  # clamped to end exactly at the bar line
    assert not notes[0].tie  # no tie-continuation was created

    assert notes[1].offset == 0.0  # measure 2, beat 0 (bar-relative)
    assert notes[1].duration.quarterLength == 1.0


def test_score_json_to_musicxml_irregular_duration_within_a_measure_still_ties(tmp_path: Path):
    # Bug D root cause (the "Playback sync unavailable" banner):
    # quantize.py's 16th-note grid (GRID_BEATS) routinely produces notated
    # durations, like "5/16" here (1.25 quarterLength), that aren't
    # representable as a single note value -- music21's writer silently
    # splits these into a tied PAIR of <note> elements (a quarter tied to a
    # sixteenth) even though the duration stays entirely WITHIN one measure
    # (unlike test_score_json_to_musicxml_clamps_bar_crossing_duration
    # above, which is a DIFFERENT, already-fixed cross-measure case this
    # test deliberately does not overlap with). One JSON event therefore
    # becomes TWO real <note> elements in the exported file -- the exact
    # mismatch apps/desktop/web/src/lib/cursorWalk.ts's
    # `isRestOrAllTiedStep` exists to tolerate in the OSMD cursor walk (see
    # that module's own tests, apps/desktop/web/src/lib/cursorWalk.test.ts).
    # This test locks in the underlying MusicXML shape that fix depends on.
    score = _sample_score()
    score["parts"][0]["measures"] = [
        {
            "number": 1,
            "events": [
                {
                    "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 1.25,
                    "notatedOnset": "0/1", "notatedDuration": "5/16", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
                {
                    "id": "note_01", "pitch": 64, "onsetSeconds": 1.25, "offsetSeconds": 2.0,
                    "notatedOnset": "5/16", "notatedDuration": "3/16", "voice": 1,
                    "confidence": 0.85, "locked": False,
                },
            ],
        },
    ]
    out_path = tmp_path / "guitar_within_measure_tie.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()

    assert '<tie type="start" />' in content
    assert '<tie type="stop" />' in content

    reopened = music21.converter.parse(str(out_path))
    notation_part = next(p for p in reopened.parts if p.id.endswith("Staff1"))
    measures = notation_part.recurse().getElementsByClass(music21.stream.Measure)
    assert len(measures) == 1  # the tie never crosses a bar line here
    # Reopened note COUNT is 3 (tie-start + tie-stop for note_00, plus
    # note_01) even though the score JSON has only 2 events -- exactly the
    # "one JSON event, multiple XML notes" shape this whole bug is about.
    notes = list(notation_part.recurse().notes)
    assert len(notes) == 3
