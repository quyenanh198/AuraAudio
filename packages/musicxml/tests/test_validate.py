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
