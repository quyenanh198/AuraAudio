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

    # A note whose duration crosses a measure boundary is written as a tied pair
    # of <note> elements by music21's notation engine; each pair is one logical
    # note, so only the tie-start (or untied) element is counted here.
    notes = [
        n for n in parsed.flatten().notes
        if n.tie is None or n.tie.type != "stop"
    ]
    if len(notes) != expected_note_count:
        raise MusicXmlValidationError(
            f"expected {expected_note_count} notes, reopened file has {len(notes)}"
        )
