"""Real-piano-timbre rendering, added during detection-quality roadmap item
2's investigation (docs/superpowers/SESSION-HANDOFF.md "Detection-quality
roadmap", docs/benchmarks/2026-08-21-dq2.md).

The committed synthetic benchmark suite's piano fixtures
(`test_fixtures.benchmark_suite`, `timbre="tone"`) use an additive
decaying-harmonic model (`test_fixtures.generate._decaying_harmonic`) --
not real piano audio. DQ-2 found this gives a misleading signal when
comparing a model trained exclusively on real piano recordings against
basic-pitch (which was itself threshold-tuned against this exact synthetic
timbre in DQ-1) -- see dq2.md's "Fixture-timbre investigation" section for
the full controlled A/B evidence (same notes/onsets/durations, timbre is
the only variable).

This module renders the SAME note specs as two of the existing synthetic
piano fixtures, but using real single-note piano recordings
(assets/real_piano_samples/, see that directory's README for license/
origin) placed at each note's onset instead of a synthesized waveform --
no pitch-shifting needed, since a real recording exists for every distinct
pitch these two fixtures use.

NOTE ON PACKAGING: unlike `test_fixtures.reference.generate_reference_clip`
(pure numpy synthesis, no external files), this module reads from
`assets/real_piano_samples/` via a path relative to this installed
package's source tree. That's fine for how this repo actually uses
`test_fixtures` -- always a `uv run --package ... ` workspace-member
invocation against the real source checkout, never a built/installed
wheel in isolation (this package is a `test`-only optional dependency of
`aura-worker`, see workers/transcription/pyproject.toml) -- but it does
mean this module is NOT usable from a wheel that dropped the `assets/`
directory. If that ever changes, package `assets/` as wheel data
(`[tool.hatch.build.targets.wheel.force-include]`) before relying on this
from anywhere else.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile

from test_fixtures.reference import NoteSpec, ReferenceClip, ReferenceClipSpec, ReferenceEvent

_ASSETS_DIR = Path(__file__).resolve().parent.parent.parent / "assets" / "real_piano_samples"
_SAMPLE_RATE = 22050
_TAIL_S = 2.0  # let a real piano note's natural decay play out, like a real performance

_NOTE_NAMES = ["C", "Cs", "D", "Ds", "E", "F", "Fs", "G", "Gs", "A", "As", "B"]


class RealPianoSampleMissingError(RuntimeError):
    """A note used by a real-piano fixture has no recorded sample under
    assets/real_piano_samples/ -- either the fixture's note range grew
    beyond the vendored set, or the assets directory is missing (see this
    module's "NOTE ON PACKAGING" docstring paragraph)."""


def midi_to_sample_name(midi_pitch: int) -> str:
    """C4 == MIDI 60, matching test_fixtures.benchmark_suite's own
    tonic_midi_base convention and the {Name}{Octave}.mp3 filenames under
    assets/real_piano_samples/ (e.g. 60 -> "C4", 61 -> "Cs4")."""
    octave = midi_pitch // 12 - 1
    name = _NOTE_NAMES[midi_pitch % 12]
    return f"{name}{octave}"


_sample_cache: dict[str, np.ndarray] = {}


def _load_sample(midi_pitch: int) -> np.ndarray:
    name = midi_to_sample_name(midi_pitch)
    if name in _sample_cache:
        return _sample_cache[name]

    path = _ASSETS_DIR / f"{name}.mp3"
    if not path.exists():
        raise RealPianoSampleMissingError(
            f"no real piano sample for MIDI {midi_pitch} ({name}) at {path} -- "
            "vendor it into assets/real_piano_samples/ (copy from "
            "apps/desktop/web/src/assets/soundfonts/piano/) before adding a "
            "real-piano fixture that uses this pitch."
        )
    import librosa  # deferred: only real_piano.py's own callers need this extra dependency

    audio, _ = librosa.load(str(path), sr=_SAMPLE_RATE, mono=True)
    _sample_cache[name] = audio
    return audio


def render_real_piano_clip(spec: ReferenceClipSpec, path: Path) -> ReferenceClip:
    """Same shape/contract as
    test_fixtures.reference.generate_reference_clip, but places a REAL
    recorded piano sample at each note's onset instead of synthesizing one.
    `spec.timbre` is ignored (real audio has no timbre parameter) --
    callers should still set it to "tone" for consistency with the rest of
    the benchmark suite's piano entries.

    Unlike the synthetic renderer, a note's `duration_s` is NOT used to
    truncate the sample -- the real recording's own natural decay plays
    out in full (like an actual performance), matching how the DQ-2
    investigation's controlled A/B probe rendered these clips."""
    if not spec.notes:
        raise ValueError("spec.notes must be non-empty")

    total_duration_s = max(onset_s + _TAIL_S for _, onset_s, _dur in spec.notes)
    n_samples = int(round(_SAMPLE_RATE * total_duration_s))
    signal = np.zeros(n_samples)
    events: list[ReferenceEvent] = []

    for midi_pitch, onset_s, duration_s in spec.notes:
        sample = _load_sample(midi_pitch)
        start_idx = int(round(onset_s * _SAMPLE_RATE))
        end_idx = min(start_idx + len(sample), n_samples)
        if end_idx > start_idx:
            signal[start_idx:end_idx] += sample[: end_idx - start_idx]
        events.append(
            ReferenceEvent(pitch=midi_pitch, onset_s=onset_s, offset_s=onset_s + duration_s)
        )

    peak = np.max(np.abs(signal))
    if peak > 0:
        signal = signal / peak * 0.8
    pcm = (signal * 32767).astype(np.int16)

    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), _SAMPLE_RATE, pcm)

    events.sort(key=lambda e: (e.onset_s, e.pitch))
    return ReferenceClip(path=path, events=events, spec=spec)


def notes_for(spec: ReferenceClipSpec) -> list[NoteSpec]:
    """Convenience passthrough -- lets a caller build a real-piano
    ReferenceClipSpec that reuses an existing synthetic spec's exact note
    list (same onsets/pitches/durations, only the renderer differs)."""
    return spec.notes
