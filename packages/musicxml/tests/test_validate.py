from pathlib import Path

import pytest

from musicxml.export import score_json_to_musicxml
from musicxml.validate import MusicXmlValidationError, reopen_and_check

from .test_export import _sample_score


def test_reopen_and_check_accepts_a_well_formed_export(tmp_path: Path):
    out_path = tmp_path / "out.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    reopen_and_check(out_path, expected_note_count=2)  # must not raise


def test_reopen_and_check_rejects_note_count_mismatch(tmp_path: Path):
    out_path = tmp_path / "out.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    with pytest.raises(MusicXmlValidationError):
        reopen_and_check(out_path, expected_note_count=99)


def test_reopen_and_check_rejects_malformed_file(tmp_path: Path):
    bad_path = tmp_path / "bad.musicxml"
    bad_path.write_text("not xml at all")
    with pytest.raises(MusicXmlValidationError):
        reopen_and_check(bad_path, expected_note_count=0)


def test_reopen_and_check_counts_a_barline_crossing_note_once(tmp_path: Path):
    from score_schema.models import build_score

    # A whole-note-length note starting on beat 4 of a 4/4 measure overruns
    # into measure 2; music21 ties it into two <note> elements on write.
    score = build_score(
        instrument="guitar",
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [{
                "id": "note_00", "pitch": 60, "onsetSeconds": 1.5, "offsetSeconds": 3.5,
                "notatedOnset": "3/4", "notatedDuration": "1/1", "voice": 1,
                "confidence": 0.9, "locked": False,
            }],
        }],
    )
    out_path = tmp_path / "tied.musicxml"
    score_json_to_musicxml(score, out_path)
    reopen_and_check(out_path, expected_note_count=1)  # must not raise
