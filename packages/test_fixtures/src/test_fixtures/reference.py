"""Reference-truth fixture generation for the transcription benchmark
harness (workers/transcription/src/aura_worker/eval/benchmark.py).

Unlike the ad hoc single-purpose generators in generate.py (one WAV, one
narrow detection question), this module produces a WAV *plus* its exact
ground-truth note events, from a declarative spec — so a benchmark can
score real pipeline output (onset/offset F1, tempo/key/meter accuracy)
against a known-correct answer instead of eyeballing it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from test_fixtures.generate import _decaying_harmonic, karplus_strong_pluck

# (midi_pitch, onset_s, duration_s)
NoteSpec = tuple[int, float, float]

_VALID_TIMBRES = frozenset({"pluck", "tone"})
_TAIL_S = 0.3  # rendered past each note's nominal duration, for natural decay


def midi_to_freq(midi_pitch: float) -> float:
    return 440.0 * (2.0 ** ((midi_pitch - 69) / 12.0))


@dataclass(frozen=True)
class ReferenceEvent:
    """One ground-truth note, in the same shape as score_schema.models.NoteEvent's
    performed-time fields (pitch/onset_s/offset_s) so scoring code can treat
    reference and predicted events uniformly."""

    pitch: int
    onset_s: float
    offset_s: float


@dataclass(frozen=True)
class ReferenceClipSpec:
    """Declarative description of one benchmark fixture."""

    name: str
    notes: list[NoteSpec]
    timbre: str  # "pluck" (guitar-like, Karplus-Strong) | "tone" (piano-ish damped sines)
    tempo_bpm: float
    meter: str
    key: str
    instrument: str  # "guitar" | "piano"
    sample_rate: int = 22050
    # "synthetic" (default -- test_fixtures.reference.generate_reference_clip,
    # this spec's `timbre` picks the waveform model) | "real_piano_sample"
    # (test_fixtures.real_piano.render_real_piano_clip -- real recorded piano
    # audio at every note, `timbre` is ignored). Added for DQ-2's
    # fixture-timbre investigation, see real_piano.py's module docstring.
    renderer: str = "synthetic"


@dataclass(frozen=True)
class ReferenceClip:
    path: Path
    events: list[ReferenceEvent]
    spec: ReferenceClipSpec = field(repr=False)


def generate_reference_clip(spec: ReferenceClipSpec, path: Path) -> ReferenceClip:
    """Synthesizes `spec` to a WAV at `path` and returns the ReferenceClip
    (WAV path + exact ground-truth events). Deterministic: the same spec
    always renders byte-identical audio (each note's synthesis is seeded
    from its position and pitch)."""
    if spec.timbre not in _VALID_TIMBRES:
        raise ValueError(
            f"unknown timbre {spec.timbre!r}, expected one of {sorted(_VALID_TIMBRES)}"
        )
    if not spec.notes:
        raise ValueError("spec.notes must be non-empty")

    sample_rate = spec.sample_rate
    total_duration_s = max(onset_s + duration_s for _, onset_s, duration_s in spec.notes) + _TAIL_S
    n_samples = int(round(sample_rate * total_duration_s))
    signal = np.zeros(n_samples)
    events: list[ReferenceEvent] = []

    for i, (midi_pitch, onset_s, duration_s) in enumerate(spec.notes):
        freq = midi_to_freq(midi_pitch)
        render_duration_s = duration_s + _TAIL_S
        if spec.timbre == "pluck":
            note_signal = karplus_strong_pluck(
                freq, render_duration_s, sample_rate=sample_rate, seed=1000 * midi_pitch + i
            )
        else:
            local_t = np.linspace(
                0, render_duration_s, int(round(sample_rate * render_duration_s)), endpoint=False
            )
            note_signal = _decaying_harmonic(local_t, freq, num_harmonics=6, decay_rate=1.2)

        start_idx = int(round(onset_s * sample_rate))
        end_idx = min(start_idx + len(note_signal), n_samples)
        if end_idx > start_idx:
            signal[start_idx:end_idx] += note_signal[: end_idx - start_idx]
        events.append(
            ReferenceEvent(pitch=midi_pitch, onset_s=onset_s, offset_s=onset_s + duration_s)
        )

    peak = np.max(np.abs(signal))
    if peak > 0:
        signal = signal / peak * 0.8
    pcm = (signal * 32767).astype(np.int16)

    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, pcm)

    events.sort(key=lambda e: (e.onset_s, e.pitch))
    return ReferenceClip(path=path, events=events, spec=spec)
