"""The curated reference-clip suite used by the transcription benchmark
harness (workers/transcription/src/aura_worker/eval/benchmark.py).

Item 0 of the detection-quality roadmap (docs/superpowers/SESSION-HANDOFF.md,
"Detection-quality roadmap") calls for "a small curated suite of ~8-12 specs
covering: monophonic melody (guitar), two-voice chords, piano two-hand
ranges, at 2-3 tempi, at least 2 keys and 2 meters, a few seconds each".
This module is that suite.

BENCHMARK_SUITE_VERSION is bumped whenever the *content* of the suite
changes (a spec's notes/tempo/key/meter/timbre, or a spec added/removed) —
later benchmark runs record which version they were scored against, so a
score change can be attributed to "the fixtures changed" vs. "detection
quality changed" (see docs/benchmarks/*.json's suite_version field).
"""
from __future__ import annotations

from test_fixtures.generate import scale_pitches
from test_fixtures.reference import NoteSpec, ReferenceClipSpec

BENCHMARK_SUITE_VERSION = "2026-08-21-v2"


def _melody_notes(
    pitches: list[int], tempo_bpm: float, beats_per_note: float = 1.0, gap_ratio: float = 0.85
) -> list[NoteSpec]:
    beat_s = 60.0 / tempo_bpm
    note_len = beats_per_note * beat_s
    return [(p, i * note_len, note_len * gap_ratio) for i, p in enumerate(pitches)]


def _chord_notes(
    chords: list[tuple[int, ...]],
    tempo_bpm: float,
    beats_per_chord: float = 1.0,
    gap_ratio: float = 0.85,
) -> list[NoteSpec]:
    beat_s = 60.0 / tempo_bpm
    chord_len = beats_per_chord * beat_s
    notes: list[NoteSpec] = []
    for i, chord in enumerate(chords):
        onset_s = i * chord_len
        duration_s = chord_len * gap_ratio
        notes.extend((pitch, onset_s, duration_s) for pitch in chord)
    return notes


def get_benchmark_suite() -> list[ReferenceClipSpec]:
    """Returns the curated suite, rebuilt fresh on every call (specs are
    small, cheap dataclasses — no need to cache)."""
    specs: list[ReferenceClipSpec] = []

    # --- Monophonic melody, guitar (pluck timbre) ------------------------
    c_major = scale_pitches("C major", tonic_midi_base=48)
    specs.append(
        ReferenceClipSpec(
            name="guitar_melody_c_major_90",
            notes=_melody_notes(c_major, tempo_bpm=90.0),
            timbre="pluck",
            tempo_bpm=90.0,
            meter="4/4",
            key="C major",
            instrument="guitar",
        )
    )

    g_major = scale_pitches("G major", tonic_midi_base=48)
    specs.append(
        ReferenceClipSpec(
            name="guitar_melody_g_major_120_3_4",
            notes=_melody_notes(g_major, tempo_bpm=120.0),
            timbre="pluck",
            tempo_bpm=120.0,
            meter="3/4",
            key="G major",
            instrument="guitar",
        )
    )

    d_major = scale_pitches("D major", tonic_midi_base=50)
    specs.append(
        ReferenceClipSpec(
            name="guitar_melody_d_major_140",
            notes=_melody_notes(d_major, tempo_bpm=140.0),
            timbre="pluck",
            tempo_bpm=140.0,
            meter="4/4",
            key="D major",
            instrument="guitar",
        )
    )

    # --- Two-voice chords (dyads), guitar --------------------------------
    a_minor = scale_pitches("A minor", tonic_midi_base=45)
    dyads = [(p, a_minor[(i + 2) % len(a_minor)]) for i, p in enumerate(a_minor)]
    specs.append(
        ReferenceClipSpec(
            name="guitar_two_voice_chords_a_minor_100",
            notes=_chord_notes(dyads, tempo_bpm=100.0),
            timbre="pluck",
            tempo_bpm=100.0,
            meter="4/4",
            key="A minor",
            instrument="guitar",
        )
    )

    e_minor = scale_pitches("E minor", tonic_midi_base=47)
    dyads_3_4 = [(p, e_minor[(i + 2) % len(e_minor)]) for i, p in enumerate(e_minor)]
    specs.append(
        ReferenceClipSpec(
            name="guitar_two_voice_chords_e_minor_110_3_4",
            notes=_chord_notes(dyads_3_4, tempo_bpm=110.0),
            timbre="pluck",
            tempo_bpm=110.0,
            meter="3/4",
            key="E minor",
            instrument="guitar",
        )
    )

    # --- Arpeggio (single-note-at-a-time triads), guitar ------------------
    a_minor_arp = scale_pitches("A minor", tonic_midi_base=45)
    arp = [a_minor_arp[i % len(a_minor_arp)] for i in (0, 2, 4, 0, 2, 4, 7, 4)]
    specs.append(
        ReferenceClipSpec(
            name="guitar_arpeggio_a_minor_130",
            notes=_melody_notes(arp, tempo_bpm=130.0, beats_per_note=0.5),
            timbre="pluck",
            tempo_bpm=130.0,
            meter="4/4",
            key="A minor",
            instrument="guitar",
        )
    )

    # --- Monophonic melody, piano (tone timbre) ---------------------------
    piano_c_major = scale_pitches("C major", tonic_midi_base=60)
    specs.append(
        ReferenceClipSpec(
            name="piano_melody_c_major_100",
            notes=_melody_notes(piano_c_major, tempo_bpm=100.0),
            timbre="tone",
            tempo_bpm=100.0,
            meter="4/4",
            key="C major",
            instrument="piano",
        )
    )

    piano_d_minor = scale_pitches("D minor", tonic_midi_base=60)
    specs.append(
        ReferenceClipSpec(
            name="piano_melody_d_minor_120",
            notes=_melody_notes(piano_d_minor, tempo_bpm=120.0),
            timbre="tone",
            tempo_bpm=120.0,
            meter="4/4",
            key="D minor",
            instrument="piano",
        )
    )

    # --- Piano two-hand ranges: LH bass note + RH dyad per chord ----------
    f_major = scale_pitches("F major", tonic_midi_base=60)
    two_hand_chords = [
        (root - 24, root, f_major[(i + 2) % len(f_major)]) for i, root in enumerate(f_major)
    ]
    specs.append(
        ReferenceClipSpec(
            name="piano_two_hand_chords_f_major_90_3_4",
            notes=_chord_notes(two_hand_chords, tempo_bpm=90.0),
            timbre="tone",
            tempo_bpm=90.0,
            meter="3/4",
            key="F major",
            instrument="piano",
        )
    )

    # --- Piano wide range: alternating low (LH) / high (RH) single notes --
    e_minor_piano = scale_pitches("E minor", tonic_midi_base=60)
    wide_range = []
    for i, p in enumerate(e_minor_piano):
        wide_range.append(p - 24 if i % 2 == 0 else p + 12)
    specs.append(
        ReferenceClipSpec(
            name="piano_two_hand_wide_range_e_minor_130",
            notes=_melody_notes(wide_range, tempo_bpm=130.0),
            timbre="tone",
            tempo_bpm=130.0,
            meter="4/4",
            key="E minor",
            instrument="piano",
        )
    )

    # --- Fast passage (16th notes), guitar + piano -----------------------
    # Added post-review (docs/benchmarks/2026-08-21-dq1b.md): the original
    # suite's fastest case was eighth notes @130bpm (0.196s notes), which
    # never stressed aura_worker.ghost_filter.MIN_DURATION_S against a
    # genuinely short real note. An ascending-then-descending one-octave
    # 16th-note run @140bpm (~0.091s nominal note length, well under
    # MIN_DURATION_S=0.15) closes that gap for both timbres.
    c_major_run = scale_pitches("C major", tonic_midi_base=48)
    c_major_run_up_and_down = c_major_run + list(reversed(c_major_run[:-1]))
    specs.append(
        ReferenceClipSpec(
            name="guitar_sixteenth_run_c_major_140",
            notes=_melody_notes(c_major_run_up_and_down, tempo_bpm=140.0, beats_per_note=0.25),
            timbre="pluck",
            tempo_bpm=140.0,
            meter="4/4",
            key="C major",
            instrument="guitar",
        )
    )

    piano_c_major_run = scale_pitches("C major", tonic_midi_base=60)
    piano_c_major_run_up_and_down = piano_c_major_run + list(reversed(piano_c_major_run[:-1]))
    specs.append(
        ReferenceClipSpec(
            name="piano_sixteenth_run_c_major_140",
            notes=_melody_notes(piano_c_major_run_up_and_down, tempo_bpm=140.0, beats_per_note=0.25),
            timbre="tone",
            tempo_bpm=140.0,
            meter="4/4",
            key="C major",
            instrument="piano",
        )
    )

    return specs
