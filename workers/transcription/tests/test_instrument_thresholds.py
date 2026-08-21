"""TDD for per-instrument basic-pitch thresholds (detection-quality roadmap
item 1). Values were chosen by a grid search over basic-pitch's own
`onset_threshold`/`frame_threshold` parameters against the 10-fixture
benchmark suite (post ghost-filtering) -- see docs/benchmarks/2026-08-21-dq1.md
for the full sweep and the DQ-1 report for the raw numbers. Both instruments
land far above basic-pitch's own defaults (onset 0.5, frame 0.3): guitar's
percussive pluck attack and piano's damped tone both confuse basic-pitch's
default onset sensitivity into raising far more ghost onsets than either
timbre actually has, and each instrument's best frame threshold differs
enough (guitar tolerates a much higher one; piano needs a much lower one)
that a single shared value could not reach either optimum.
"""
from __future__ import annotations

from aura_worker.instrument_thresholds import (
    DEFAULT_THRESHOLDS,
    GUITAR_THRESHOLDS,
    PIANO_THRESHOLDS,
    thresholds_for_instrument,
)


def test_guitar_thresholds():
    assert thresholds_for_instrument("guitar") == GUITAR_THRESHOLDS


def test_piano_thresholds():
    assert thresholds_for_instrument("piano") == PIANO_THRESHOLDS


def test_unknown_instrument_falls_back_to_default():
    assert thresholds_for_instrument("ukulele") == DEFAULT_THRESHOLDS


def test_guitar_and_piano_thresholds_differ():
    # The whole point of per-instrument tuning -- if these ever collapse to
    # the same values, a single shared constant would be simpler and this
    # module's reason to exist should be re-examined.
    assert GUITAR_THRESHOLDS != PIANO_THRESHOLDS


def test_all_threshold_values_are_valid_probabilities():
    for thresholds in (DEFAULT_THRESHOLDS, GUITAR_THRESHOLDS, PIANO_THRESHOLDS):
        assert 0.0 < thresholds.onset_threshold <= 1.0
        assert 0.0 < thresholds.frame_threshold <= 1.0


def test_default_thresholds_match_basic_pitch_library_defaults():
    # DEFAULT_THRESHOLDS is the fallback for any instrument this module
    # doesn't have a tuned entry for -- it should reproduce basic-pitch's
    # own untouched defaults, not silently apply a tuned value.
    assert DEFAULT_THRESHOLDS.onset_threshold == 0.5
    assert DEFAULT_THRESHOLDS.frame_threshold == 0.3
