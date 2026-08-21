"""Per-instrument basic-pitch onset/frame thresholds.

Detection-quality roadmap item 1 (docs/superpowers/SESSION-HANDOFF.md
"Detection-quality roadmap"). basic_pitch.inference.predict's own defaults
(`onset_threshold=0.5, frame_threshold=0.3`) are tuned for its general
training distribution, not for this project's two specific timbres. A grid
search over both parameters (applied together with
`aura_worker.ghost_filter.filter_ghost_notes`, since that's how the
pipeline actually uses them) found a sharp, well-separated optimum per
instrument. The search is a real, rerunnable script --
`workers/transcription/scripts/tune_instrument_thresholds.py` -- and its
last recorded output is committed at
`docs/benchmarks/2026-08-21-threshold-sweep.md`; every number below is
read directly from that file, not from memory:

- **Guitar** (Karplus-Strong pluck timbre): onset_threshold=0.8 raised mean
  onset F1 from 0.675 (default onset/frame, measured on the enlarged
  7-fixture guitar set that now includes the fast-passage fixture -- see
  the RE-DERIVATION note below) to 0.943; frame_threshold=0.4 pushed it
  further to 0.966 (min-fixture 0.857) -- a percussive pluck's onset
  energy is naturally much sharper than basic-pitch's default expects, so
  the default onset threshold passes through far more spurious onsets
  (re-attacks during the pluck's long decay tail, per
  `aura_worker.ghost_filter`'s diagnosis) than a guitar performance
  actually has.
- **Piano** (damped-tone timbre): onset_threshold=0.8 raised mean onset F1
  from 0.554 (default onset/frame, same enlarged-suite measurement) to
  0.807; frame_threshold=0.1 pushed the mean further to 0.950 measured on
  the ORIGINAL 4-fixture piano set (before the fast-passage fixture
  existed) -- a damped tone's frame energy decays faster than a plucked
  string's, so piano needs a much more permissive frame threshold than
  guitar to keep tracking a note through its natural decay instead of
  truncating it early.

RE-DERIVATION / KNOWN LIMITATION (post-review, after 2 fast-passage
fixtures were added to `test_fixtures.benchmark_suite` -- see
`aura_worker.ghost_filter`'s own RE-DERIVATION note): re-running the sweep
against the enlarged 12-fixture suite found piano's frame_threshold=0.1 is
NOT a robust choice once a genuinely fast passage is included --
`piano_sixteenth_run_c_major_140` scores only 0.476 at frame=0.1, while
frame=0.2 would score 0.85 on that same fixture (and a *higher* 12-fixture
mean overall, 0.871 vs 0.855). This is a real, measured trade-off, not
hidden: frame=0.2 is worse on the ORIGINAL 4 piano fixtures specifically
(mean 0.8775 there vs. 0.95 at frame=0.1 -- `melody_c_major_100` alone
would drop from 1.000 to ~0.94, `two_hand_wide_range` from 1.000 to ~0.80),
which would breach DQ-1's own "no fixture drops >0.05" gate against the
already-measured, already-committed `docs/benchmarks/2026-08-21-dq1.md`
numbers. PIANO_THRESHOLDS is therefore left at frame_threshold=0.1,
deliberately, protecting the verified original-suite result over the new
fast-passage fixture's score -- piano's poor recall on very fast passages
(0.476 onset F1 on `piano_sixteenth_run_c_major_140`, see
`docs/benchmarks/2026-08-21-dq1b.md`) is a real, disclosed, NOT-fixed-here
limitation of this stage, not silently dropped notes further downstream --
see `ghost_filter`'s docstring for why the ghost-note duration floor
specifically is not the cause of it.

Both instruments' optimum needed an onset threshold far above the library
default, but their optimal frame thresholds are the near-opposite of each
other (guitar: higher than default 0.3; piano: much lower) -- exactly the
kind of case a single shared threshold cannot serve, motivating the
per-instrument table below over one global constant.

METHODOLOGY CAVEAT: as with `ghost_filter`, every value here is tuned and
gated on the same synthetic benchmark suite it is measured against -- no
held-out fixture set, no real-recording manifest validation yet.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BasicPitchThresholds:
    onset_threshold: float
    frame_threshold: float


# basic_pitch.inference.predict's own untouched defaults -- used for any
# instrument this module has no tuned entry for, so an unrecognized
# instrument degrades to library-default behavior rather than silently
# picking one of the two tuned profiles.
DEFAULT_THRESHOLDS = BasicPitchThresholds(onset_threshold=0.5, frame_threshold=0.3)

GUITAR_THRESHOLDS = BasicPitchThresholds(onset_threshold=0.8, frame_threshold=0.4)
PIANO_THRESHOLDS = BasicPitchThresholds(onset_threshold=0.8, frame_threshold=0.1)

_THRESHOLDS_BY_INSTRUMENT: dict[str, BasicPitchThresholds] = {
    "guitar": GUITAR_THRESHOLDS,
    "piano": PIANO_THRESHOLDS,
}


def thresholds_for_instrument(instrument: str) -> BasicPitchThresholds:
    """Returns the tuned thresholds for `instrument`, or
    `DEFAULT_THRESHOLDS` (basic-pitch's own library defaults) for any
    instrument not in the tuned table."""
    return _THRESHOLDS_BY_INSTRUMENT.get(instrument, DEFAULT_THRESHOLDS)
