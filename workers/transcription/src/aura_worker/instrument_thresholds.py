"""Per-instrument basic-pitch onset/frame thresholds.

Detection-quality roadmap item 1 (docs/superpowers/SESSION-HANDOFF.md
"Detection-quality roadmap"). basic_pitch.inference.predict's own defaults
(`onset_threshold=0.5, frame_threshold=0.3`) are tuned for its general
training distribution, not for this project's two specific timbres. A grid
search over both parameters against the 10-fixture curated benchmark suite
(applied together with `aura_worker.ghost_filter.filter_ghost_notes`, since
that's how the pipeline actually uses them) found a sharp, well-separated
optimum per instrument -- see docs/benchmarks/2026-08-21-dq1.md for the full
sweep table:

- **Guitar** (Karplus-Strong pluck timbre): onset_threshold=0.8 raised mean
  onset F1 from 0.615 (default) to 0.962; frame_threshold=0.4 pushed it
  further to 0.985 (min-fixture 0.938) -- a percussive pluck's onset energy
  is naturally much sharper than basic-pitch's default expects, so the
  default onset threshold passes through far more spurious onsets
  (re-attacks during the pluck's long decay tail, per
  `aura_worker.ghost_filter`'s diagnosis) than a guitar performance
  actually has.
- **Piano** (damped-tone timbre): onset_threshold=0.8 raised mean onset F1
  from 0.426 (default) to 0.798; frame_threshold=0.1 pushed it further to
  0.950 (min-fixture 0.857) -- confirmed as a genuine sharp optimum, not
  sweep noise, by a finer grid around it (0.05/0.08 both collapse to 0.662,
  0.1 spikes to 0.950, 0.12 stays close at 0.938, 0.15 falls off to 0.884).
  A damped tone's frame energy decays faster than a plucked string's, so
  piano needs a much more permissive frame threshold than guitar to keep
  tracking a note through its natural decay instead of truncating it (which
  would otherwise drop notes below `ghost_filter.MIN_DURATION_S`).

Both instruments' optimum needed an onset threshold far above the library
default, but their optimal frame thresholds are the near-opposite of each
other (guitar: higher than default 0.3; piano: much lower) -- exactly the
kind of case a single shared threshold cannot serve, motivating the
per-instrument table below over one global constant.
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
