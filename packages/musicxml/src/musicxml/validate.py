from __future__ import annotations

from pathlib import Path

from music21 import clef, converter


class MusicXmlValidationError(ValueError):
    pass


def reopen_and_check(path: Path, expected_note_count: int) -> None:
    try:
        parsed = converter.parse(str(path))
    except Exception as exc:
        raise MusicXmlValidationError(f"failed to reopen {path}: {exc}") from exc

    # Guitar exports carry a second TAB staff (musicxml.export, R1) that
    # mirrors every note from the notation staff for display purposes —
    # counting it too would double every guitar note, so parts whose clef
    # is a TabClef are excluded from the physical-note count entirely.
    notated_parts = [
        part for part in parsed.parts
        if not isinstance(part.flatten().getElementsByClass(clef.Clef).first(), clef.TabClef)
    ]

    # A note whose duration crosses a measure boundary is written as a tied pair
    # of <note> elements by music21's notation engine; each pair is one logical
    # note, so only the tie-start (or untied) element is counted here. A Chord
    # (musicxml.export, R3 — same-onset events grouped into one chord) is one
    # element in `.notes` but represents one original event per pitch, so it
    # contributes len(n.pitches) rather than 1.
    notes = [
        pitch
        for part in notated_parts
        for n in part.flatten().notes
        if n.tie is None or n.tie.type != "stop"
        for pitch in n.pitches
    ]
    if len(notes) != expected_note_count:
        raise MusicXmlValidationError(
            f"expected {expected_note_count} notes, reopened file has {len(notes)}"
        )
