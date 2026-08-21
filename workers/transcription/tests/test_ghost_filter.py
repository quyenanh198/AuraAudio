"""TDD for the ghost-note filter (detection-quality roadmap item 1).

Diagnosis backing these thresholds (see docs/benchmarks/2026-08-21-dq1.md and
the DQ-1 report): across the 10-fixture benchmark suite, raw basic-pitch
NoteEvents split into 104 true-positive notes (onset-matched against ground
truth) and 178 unmatched "ghost" notes. The dominant ghost shapes were (a)
high-pitch, low-confidence harmonic/overtone artifacts (confidence 0.28-0.41)
and (b) a decaying note's sustain re-detected as a spurious second onset. A
smaller but real pattern was octave-shadow: a much-lower-confidence note
exactly an octave from a simultaneous stronger note. Every true positive in
the suite had confidence >= 0.365 and duration >= 0.186s, so MIN_CONFIDENCE
and MIN_DURATION_S below were both chosen as the tightest round threshold
that removed zero true positives on that measured suite.
"""
from __future__ import annotations

from score_schema.models import NoteEvent

from aura_worker.ghost_filter import (
    MIN_CONFIDENCE,
    MIN_DURATION_S,
    OCTAVE_CONFIDENCE_RATIO,
    OCTAVE_SEMITONES,
    OCTAVE_SIMULTANEITY_S,
    filter_ghost_notes,
)


def _note(pitch, onset_s, offset_s, confidence=0.8, velocity=90) -> NoteEvent:
    return NoteEvent(pitch=pitch, onset_s=onset_s, offset_s=offset_s, velocity=velocity, confidence=confidence)


def test_keeps_a_confident_normal_duration_note():
    notes = [_note(60, 0.0, 0.5, confidence=0.8)]
    assert filter_ghost_notes(notes) == notes


def test_drops_a_note_below_the_confidence_floor():
    notes = [_note(60, 0.0, 0.5, confidence=MIN_CONFIDENCE - 0.01)]
    assert filter_ghost_notes(notes) == []


def test_keeps_a_note_exactly_at_the_confidence_floor():
    notes = [_note(60, 0.0, 0.5, confidence=MIN_CONFIDENCE)]
    assert filter_ghost_notes(notes) == notes


def test_drops_a_note_below_the_duration_floor():
    notes = [_note(60, 0.0, MIN_DURATION_S - 0.01, confidence=0.8)]
    assert filter_ghost_notes(notes) == []


def test_keeps_a_note_exactly_at_the_duration_floor():
    notes = [_note(60, 0.0, MIN_DURATION_S, confidence=0.8)]
    assert filter_ghost_notes(notes) == notes


def test_drops_a_weak_octave_shadow_of_a_simultaneous_stronger_note():
    strong = _note(60, 0.0, 0.5, confidence=0.8)
    shadow = _note(60 + OCTAVE_SEMITONES, 0.0, 0.5, confidence=0.8 * OCTAVE_CONFIDENCE_RATIO - 0.01)
    result = filter_ghost_notes([strong, shadow])
    assert result == [strong]


def test_keeps_two_octave_apart_notes_when_confidence_is_comparable():
    # A deliberately octave-doubled performance (e.g. bass + melody) should
    # survive -- the shadow filter only fires when the weaker note is much
    # less confident, not merely somewhat less confident.
    low = _note(48, 0.0, 0.5, confidence=0.8)
    high = _note(60, 0.0, 0.5, confidence=0.75)
    result = filter_ghost_notes([low, high])
    assert result == [low, high]


def test_octave_shadow_requires_simultaneity():
    strong = _note(60, 0.0, 0.5, confidence=0.8)
    # same pitch relationship, but onsets far enough apart to not be a shadow
    later = _note(72, OCTAVE_SIMULTANEITY_S + 1.0, 1.5, confidence=0.2)
    # `later`'s own confidence is below MIN_CONFIDENCE so drop it via a
    # confidence bump to isolate the octave-shadow logic specifically
    later_confident = NoteEvent(
        pitch=later.pitch, onset_s=later.onset_s, offset_s=later.offset_s,
        velocity=later.velocity, confidence=MIN_CONFIDENCE,
    )
    result = filter_ghost_notes([strong, later_confident])
    assert later_confident in result


def test_octave_shadow_pitch_must_be_exactly_an_octave():
    strong = _note(60, 0.0, 0.5, confidence=0.9)
    near_octave = _note(71, 0.0, 0.5, confidence=0.4)  # 11 semitones, not 12
    result = filter_ghost_notes([strong, near_octave])
    assert near_octave in result


def test_empty_input_returns_empty_output():
    assert filter_ghost_notes([]) == []


def test_preserves_order_of_surviving_notes():
    a = _note(60, 0.0, 0.5, confidence=0.8)
    b = _note(64, 1.0, 1.5, confidence=0.8)
    c = _note(67, 2.0, 2.5, confidence=0.8)
    assert filter_ghost_notes([a, b, c]) == [a, b, c]
