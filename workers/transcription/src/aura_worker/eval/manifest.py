"""Optional external benchmark manifest: local real recordings + reference
MIDI, listed in a small JSON file that is gitignored and absent by default
(see docs/superpowers/SESSION-HANDOFF.md's "Detection-quality roadmap" item
0). This module only loads/parses; benchmark.py wires it into the harness
when `--manifest <path>` is passed.

Manifest format::

    {
      "entries": [
        {
          "name": "my_real_riff",
          "audio_path": "riff.wav",
          "reference_midi_path": "riff_reference.mid",
          "instrument": "guitar"
        },
        ...
      ]
    }

`audio_path`/`reference_midi_path` may be relative (resolved against the
manifest file's own directory) or absolute. `instrument` must be "guitar"
or "piano" (the only two score_schema.models.Project.instrument values the
pipeline supports).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from test_fixtures.reference import ReferenceEvent

_VALID_INSTRUMENTS = frozenset({"guitar", "piano"})
_REQUIRED_FIELDS = ("name", "audio_path", "reference_midi_path", "instrument")


@dataclass(frozen=True)
class ManifestEntry:
    name: str
    audio_path: Path
    reference_midi_path: Path
    instrument: str


def _resolve(base_dir: Path, raw_path: str) -> Path:
    p = Path(raw_path)
    return p if p.is_absolute() else (base_dir / p)


def load_manifest(manifest_path: Path) -> list[ManifestEntry]:
    raw = json.loads(Path(manifest_path).read_text())
    if not isinstance(raw, dict) or "entries" not in raw:
        raise ValueError(f"manifest {manifest_path}: must be a JSON object with an 'entries' array")

    entries_raw = raw["entries"]
    if not isinstance(entries_raw, list):
        raise ValueError(f"manifest {manifest_path}: 'entries' must be a list")

    base_dir = Path(manifest_path).parent
    entries: list[ManifestEntry] = []
    for i, item in enumerate(entries_raw):
        if not isinstance(item, dict):
            raise ValueError(f"manifest {manifest_path}: entry {i} must be an object")
        for field_name in _REQUIRED_FIELDS:
            if field_name not in item:
                raise ValueError(
                    f"manifest {manifest_path}: entry {i} missing required field {field_name!r}"
                )
        instrument = item["instrument"]
        if instrument not in _VALID_INSTRUMENTS:
            raise ValueError(
                f"manifest {manifest_path}: entry {i} has invalid instrument {instrument!r}, "
                f"expected one of {sorted(_VALID_INSTRUMENTS)}"
            )
        entries.append(
            ManifestEntry(
                name=item["name"],
                audio_path=_resolve(base_dir, item["audio_path"]),
                reference_midi_path=_resolve(base_dir, item["reference_midi_path"]),
                instrument=instrument,
            )
        )
    return entries


def load_reference_events_from_midi(midi_path: Path) -> list[ReferenceEvent]:
    """Extracts ground-truth (pitch, onset_s, offset_s) events from a
    reference MIDI file. Iterating a `mido.MidiFile` yields each message's
    `.time` as a tempo-aware delta in SECONDS (not raw ticks), across every
    track merged in playback order — verified directly against a
    constructed 120bpm fixture during this module's own tests, not assumed
    from mido's docs. Repeated notes at the same pitch are paired
    onset-to-offset in FIFO order (first open note_on closed by the next
    note_off/zero-velocity note_on for that pitch)."""
    import mido

    midi_file = mido.MidiFile(str(midi_path))
    open_onsets: dict[tuple[int, int], list[float]] = {}
    events: list[ReferenceEvent] = []
    now = 0.0

    for msg in midi_file:
        now += msg.time
        is_note_on = msg.type == "note_on" and msg.velocity > 0
        is_note_off = msg.type == "note_off" or (msg.type == "note_on" and msg.velocity == 0)
        if is_note_on:
            open_onsets.setdefault((msg.channel, msg.note), []).append(now)
        elif is_note_off:
            key = (msg.channel, msg.note)
            onsets = open_onsets.get(key)
            if onsets:
                onset_s = onsets.pop(0)
                events.append(ReferenceEvent(pitch=msg.note, onset_s=onset_s, offset_s=now))

    events.sort(key=lambda e: (e.onset_s, e.pitch))
    return events
