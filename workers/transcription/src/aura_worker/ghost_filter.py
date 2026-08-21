"""Ghost-note filtering for raw basic-pitch predictions.

Detection-quality roadmap item 1 (docs/superpowers/SESSION-HANDOFF.md
"Detection-quality roadmap"). Diagnosis, run against the DQ-0 benchmark
suite's 10 fixtures (see docs/benchmarks/2026-08-21-dq1.md for the full
table): mean onset F1 was low (0.540) almost entirely because of poor
*precision* (0.24-0.59 per fixture) against near-perfect *recall*
(0.75-1.0 per fixture) -- basic-pitch's raw note events are dominated by
extra, unmatched "ghost" notes, not by missed real ones. Across all 10
fixtures, 104 predicted notes matched a ground-truth onset/pitch and 178
did not. Two shapes accounted for most of the 178:

1. High-pitch, low-confidence harmonic/overtone artifacts an octave (or
   more) above a real note -- observed confidence 0.28-0.41, well below
   every matched note's confidence (minimum observed: 0.365).
2. A decaying note's sustain re-detected as a spurious second onset near
   the next real onset, or an overtone sounding at exactly an octave above
   a real, simultaneous, much-more-confident note.

This module applies three independent, conservative filters -- confidence
floor, duration floor, octave-shadow dedupe -- chosen from that same
measurement, each verified to remove zero of the 104 true positives on the
curated suite (see test_ghost_filter.py and the DQ-1 report's constant
derivation table). It intentionally does NOT attempt to catch the
same-pitch "decaying sustain re-attack" ghost shape (pattern 2's first
half) -- that pattern's confidence and duration overlap real notes' too
much to filter safely without a per-note "is this the same performed note
continuing" heuristic, which is out of this item's scope; see the DQ-1
report's diagnosis section for the measured residual.

RE-DERIVATION (post-review, docs/benchmarks/2026-08-21-dq1b.md): code
review flagged that MIN_DURATION_S was derived from a suite whose fastest
case was eighth notes @130bpm (0.196s notes) and could plausibly delete a
genuine fast note (e.g. 16th notes: 0.125s @120bpm, 0.083s @180bpm) that
never got a chance to prove otherwise. Two 16th-note-run fixtures
(`guitar_sixteenth_run_c_major_140`, `piano_sixteenth_run_c_major_140` --
nominal note length ~0.091s, well under MIN_DURATION_S) were added to
`test_fixtures.benchmark_suite` specifically to stress this. Re-measuring
against basic-pitch's REAL raw output (at the tuned per-instrument
thresholds -- see instrument_thresholds.py) on the enlarged 12-fixture
suite found the true positive/ghost separation holds with the existing
value, and additionally explains WHY: basic-pitch's own onset/frame
detection (not this filter) is the actual bottleneck on very fast
passages -- at the current thresholds it never emits a raw note anywhere
near the ~0.09s nominal length for these fixtures; consecutive close
onsets get merged into fewer, longer detected notes instead (observed raw
durations 0.15-0.4s on the 16th-note fixtures, same range as normal-tempo
notes). Measured on the enlarged suite:
  - smallest TRUE POSITIVE raw duration: 0.1858s (from
    guitar_sixteenth_run_c_major_140 -- still comfortably above
    MIN_DURATION_S, i.e. the floor is not what limits fast-passage
    recall).
  - largest GHOST raw duration below the floor: 0.1393s (from the
    enlarged suite generally, well separated from the true-positive
    floor above).
0.15 sits cleanly between those two measured values with margin on both
sides (+0.011s above the ghost ceiling, -0.036s below the true-positive
floor), so MIN_DURATION_S is unchanged -- the enlarged suite CONFIRMS this
value rather than requiring a new one. The fast-passage recall loss that
does occur (guitar_sixteenth_run: onset F1 0.857; piano_sixteenth_run:
onset F1 0.476 -- see docs/benchmarks/2026-08-21-dq1b.md) is basic-pitch's
own onset-merging behavior at very fast tempi, not this module's doing;
out of this item's scope to fix (would need basic-pitch's own
`minimum_note_length` parameter tuned per instrument too, or a genuinely
different onset-detection approach).

METHODOLOGY CAVEAT: every constant in this module (and in
instrument_thresholds.py) is tuned and gated on the same synthetic
benchmark suite it is measured against -- there is no held-out fixture set,
and no real-recording manifest run has validated these values yet (see
docs/superpowers/SESSION-HANDOFF.md's item 1 entry). Treat "removes zero
true positives" as "on this suite", not as a universal guarantee.
"""
from __future__ import annotations

from score_schema.models import NoteEvent

# Every true positive across the 10-fixture curated benchmark suite had
# confidence >= 0.365 (see docs/benchmarks/2026-08-21-dq1.md). 0.35 is the
# largest round threshold below that measured floor -- it removes zero true
# positives on the measured suite while still cutting the weakest ghost
# notes (harmonic/overtone artifacts, typically 0.28-0.41 confidence).
MIN_CONFIDENCE = 0.35

# Re-derived against the enlarged 12-fixture suite (see this module's
# docstring "RE-DERIVATION" paragraph): the smallest true-positive raw
# duration measured is 0.1858s (from a genuine 16th-note run fixture added
# specifically to stress this), and the largest ghost duration below that
# is 0.1393s. 0.15s sits cleanly between the two with margin on both
# sides, so it is kept rather than moved. basic-pitch's own
# `minimum_note_length` default (127.7ms) already filters shorter notes
# before they reach this stage; this floor is a second, independent line
# of defense that also documents the assumption in one place. NOT a
# tempo-independent guarantee -- see the docstring's methodology caveat.
MIN_DURATION_S = 0.15

# A note an octave (12 semitones -- MIDI pitches are already rounded to
# integers by the inference stage, so this is an exact match, not a
# tolerance band) above or below a simultaneous note is very likely that
# note's harmonic overtone being mis-detected as its own note, not a
# genuinely octave-doubled performance.
OCTAVE_SEMITONES = 12

# "Simultaneous" for octave-shadow purposes: onsets within this many seconds
# of each other. Matches the benchmark harness's own onset-matching
# tolerance (aura_worker.eval.metrics.onset_f1's default
# onset_tolerance_s=0.05) so "simultaneous" means the same thing here as it
# does when the benchmark scores onsets.
OCTAVE_SIMULTANEITY_S = 0.05

# The shadow note's confidence must be below this fraction of the stronger
# note's confidence to be dropped -- "much lower", not just "somewhat
# lower", so a real octave-doubled performance (comparable confidence on
# both notes) survives. 0.75 was chosen so the diagnosis's real octave-
# shadow example (0.392 vs. a simultaneous 0.715) is caught with margin,
# while two independently-confident notes a genuine octave apart are not.
#
# KNOWN RISK (unmitigated): this ratio was tuned against exactly one
# observed example, not a distribution, and basic-pitch's `confidence`
# (mean frame activation) has no principled way to distinguish "a harmonic
# overtone mis-detected as its own note" from "a real note played much
# more softly an octave away from a louder one" -- both look identical to
# this heuristic (low confidence, exact octave, simultaneous onset). A
# genuinely soft octave-doubled note WILL be deleted by this filter today.
# Mitigation: deleted notes are not permanently lost -- they are
# recoverable through the desktop app's editor (add-note flow, Task 7 of
# the semantic-editing sub-project), so the failure mode is "extra manual
# correction", not silent, unrecoverable data loss. No suite fixture
# currently exercises a genuine soft octave-doubled note, so this risk is
# not benchmark-covered; a real-recording manifest run (see the module
# docstring's methodology caveat) is the next opportunity to find out
# whether it matters in practice.
OCTAVE_CONFIDENCE_RATIO = 0.75


def filter_ghost_notes(notes: list[NoteEvent]) -> list[NoteEvent]:
    """Removes ghost notes from raw inference-stage predictions.

    Applies the confidence and duration floors first (independent,
    per-note checks), then dedupes octave shadows among the survivors
    (a per-pair check, since it depends on which other notes are still
    present). Order of the input is preserved.
    """
    survivors = [
        note
        for note in notes
        if note.confidence >= MIN_CONFIDENCE and (note.offset_s - note.onset_s) >= MIN_DURATION_S
    ]
    return _drop_octave_shadows(survivors)


def _is_octave_shadow(note: NoteEvent, other: NoteEvent) -> bool:
    return (
        abs(other.pitch - note.pitch) == OCTAVE_SEMITONES
        and abs(other.onset_s - note.onset_s) <= OCTAVE_SIMULTANEITY_S
        and note.confidence < OCTAVE_CONFIDENCE_RATIO * other.confidence
    )


def _drop_octave_shadows(notes: list[NoteEvent]) -> list[NoteEvent]:
    shadow_indices = {
        i
        for i, note in enumerate(notes)
        if any(_is_octave_shadow(note, other) for j, other in enumerate(notes) if j != i)
    }
    return [note for i, note in enumerate(notes) if i not in shadow_indices]
