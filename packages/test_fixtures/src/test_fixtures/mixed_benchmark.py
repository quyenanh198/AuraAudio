"""The mixed (instrument + interference) fixture suite for the
detection-quality benchmark's source-separation item (docs/superpowers/
SESSION-HANDOFF.md "Detection-quality roadmap" item 3,
docs/benchmarks/2026-08-21-dq3.md).

Each fixture layers a synthesized interference bed (see test_fixtures.mixed)
on top of a clean instrument recording -- ground truth is always the
instrument's own notes only. These exist specifically to measure whether
opt-in source separation (aura_worker.separation) helps or hurts real
mixed-recording transcription, which the main curated suite
(test_fixtures.benchmark_suite, all clean single-instrument fixtures)
cannot answer on its own.

MIXED_BENCHMARK_SUITE_VERSION follows the same bump-on-content-change
convention as BENCHMARK_SUITE_VERSION.
"""
from __future__ import annotations

from test_fixtures.generate import scale_pitches
from test_fixtures.mixed import MixedClipSpec
from test_fixtures.reference import NoteSpec, ReferenceClipSpec

MIXED_BENCHMARK_SUITE_VERSION = "2026-08-21-v1"


def _melody_notes(pitches: list[int], tempo_bpm: float, beats_per_note: float = 1.0, gap_ratio: float = 0.85) -> list[NoteSpec]:
    beat_s = 60.0 / tempo_bpm
    note_len = beats_per_note * beat_s
    return [(p, i * note_len, note_len * gap_ratio) for i, p in enumerate(pitches)]


def _chord_notes(chords: list[tuple[int, ...]], tempo_bpm: float, beats_per_chord: float = 1.0, gap_ratio: float = 0.85) -> list[NoteSpec]:
    beat_s = 60.0 / tempo_bpm
    chord_len = beats_per_chord * beat_s
    notes: list[NoteSpec] = []
    for i, chord in enumerate(chords):
        onset_s = i * chord_len
        duration_s = chord_len * gap_ratio
        notes.extend((pitch, onset_s, duration_s) for pitch in chord)
    return notes


def get_mixed_benchmark_suite() -> list[MixedClipSpec]:
    """Returns the curated mixed-fixture suite, rebuilt fresh on every call."""
    specs: list[MixedClipSpec] = []

    # --- Guitar pluck melody + sung "vocals" + percussion clicks ---------
    c_major = scale_pitches("C major", tonic_midi_base=48)
    specs.append(
        MixedClipSpec(
            name="guitar_melody_mixed_vocal_percussion",
            base=ReferenceClipSpec(
                name="guitar_melody_mixed_vocal_percussion_base",
                notes=_melody_notes(c_major, tempo_bpm=100.0),
                timbre="pluck",
                tempo_bpm=100.0,
                meter="4/4",
                key="C major",
                instrument="guitar",
                sample_rate=22050,
            ),
            interference_kind="vocal_percussion",
        )
    )

    # --- Piano two-hand chords (REAL piano samples, per DQ-2's
    # fixture-timbre lesson: a real recorded timbre is a more faithful
    # proxy for real-world separation behavior than a synthetic decaying
    # harmonic) + sung "vocals" + percussion clicks --------------------
    f_major = scale_pitches("F major", tonic_midi_base=60)
    two_hand_chords = [
        (root - 24, root, f_major[(i + 2) % len(f_major)]) for i, root in enumerate(f_major)
    ]
    specs.append(
        MixedClipSpec(
            name="piano_chords_mixed_vocal_percussion",
            base=ReferenceClipSpec(
                name="piano_chords_mixed_vocal_percussion_base",
                notes=_chord_notes(two_hand_chords, tempo_bpm=90.0),
                timbre="tone",
                tempo_bpm=90.0,
                meter="3/4",
                key="F major",
                instrument="piano",
                sample_rate=22050,
                renderer="real_piano_sample",
            ),
            interference_kind="vocal_percussion",
        )
    )

    # --- Guitar arpeggio over a sustained pad chord -----------------------
    a_minor_arp = scale_pitches("A minor", tonic_midi_base=45)
    arp = [a_minor_arp[i % len(a_minor_arp)] for i in (0, 2, 4, 0, 2, 4, 7, 4)]
    specs.append(
        MixedClipSpec(
            name="guitar_arpeggio_mixed_pad",
            base=ReferenceClipSpec(
                name="guitar_arpeggio_mixed_pad_base",
                notes=_melody_notes(arp, tempo_bpm=130.0, beats_per_note=0.5),
                timbre="pluck",
                tempo_bpm=130.0,
                meter="4/4",
                key="A minor",
                instrument="guitar",
                sample_rate=22050,
            ),
            interference_kind="pad",
            interference_gain=0.30,
        )
    )

    return specs
