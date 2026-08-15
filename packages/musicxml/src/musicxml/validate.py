from __future__ import annotations

from pathlib import Path

from music21 import converter


class MusicXmlValidationError(ValueError):
    pass


def reopen_and_check(path: Path, expected_note_count: int) -> None:
    try:
        parsed = converter.parse(str(path))
    except Exception as exc:
        raise MusicXmlValidationError(f"failed to reopen {path}: {exc}") from exc

    notes = list(parsed.flatten().notes)
    if len(notes) != expected_note_count:
        raise MusicXmlValidationError(
            f"expected {expected_note_count} notes, reopened file has {len(notes)}"
        )
