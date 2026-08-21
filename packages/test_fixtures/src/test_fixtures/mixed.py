"""Mixed (instrument + interference) fixture rendering for the
detection-quality benchmark's source-separation item (docs/superpowers/
SESSION-HANDOFF.md "Detection-quality roadmap" item 3,
docs/benchmarks/2026-08-21-dq3.md).

Layers a synthesized interference bed (sustained "vocal" tones, percussion
clicks, or a sustained pad chord) on top of an existing clean
ReferenceClipSpec's rendered audio -- the instrument's own note events are
untouched (ground truth is always the instrument's notes only; interference
carries no notes of its own), so the *only* thing a mixed fixture changes
relative to its clean counterpart is what's added on top in the mix.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy.io import wavfile

from test_fixtures.reference import ReferenceClip, ReferenceClipSpec, midi_to_freq

_INTERFERENCE_KINDS = frozenset({"vocal_percussion", "pad"})


@dataclass(frozen=True)
class MixedClipSpec:
    """A clean ReferenceClipSpec plus an interference layer to mix on top.
    `name` is this mixed fixture's own name (distinct from `base.name`)."""

    name: str
    base: ReferenceClipSpec
    interference_kind: str  # "vocal_percussion" | "pad"
    interference_gain: float = 0.35
    seed: int = 7


def synth_vocal_interference(duration_s: float, sample_rate: int, seed: int = 7) -> np.ndarray:
    """Sustained, vaguely "sung" tones: a handful of held notes with light
    vibrato and a couple of harmonics, in a different register than this
    project's guitar/piano fixtures (alto-ish, MIDI 62-67) so it plausibly
    overlaps the instrument's frequency range without being identical to
    it -- not a real voice model, just enough spectral/temporal presence to
    genuinely challenge a transcription model the way a real vocal track
    would."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    sig = np.zeros_like(t)
    pitches = [64, 67, 65, 62]
    note_len = duration_s / len(pitches)
    for i, p in enumerate(pitches):
        start = i * note_len
        local_t = t - start
        mask = (local_t >= 0) & (local_t < note_len + 0.2)
        f0 = midi_to_freq(p)
        vibrato = 1.0 + 0.01 * np.sin(2 * np.pi * 5.5 * local_t)
        envelope = np.clip(np.minimum(local_t / 0.08, (note_len + 0.2 - local_t) / 0.15), 0, 1)
        voice = (
            np.sin(2 * np.pi * f0 * vibrato * local_t)
            + 0.5 * np.sin(2 * np.pi * f0 * 2 * vibrato * local_t)
            + 0.3 * np.sin(2 * np.pi * f0 * 3 * vibrato * local_t)
        )
        sig[mask] += (envelope * voice)[mask]
    _ = rng  # reserved for future randomized variants; unused today
    return sig


def synth_percussion_interference(duration_s: float, sample_rate: int, seed: int = 13) -> np.ndarray:
    """A steady 2-hits/sec kick/hihat-ish click pattern -- noise bursts, not
    tonal, so it stresses onset detection (transient competition) rather
    than pitch confusion."""
    rng = np.random.default_rng(seed)
    t_full = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    sig = np.zeros_like(t_full)
    step = 0.5
    n = int(duration_s / step)
    click_len = 0.08
    n_click = int(click_len * sample_rate)
    for i in range(n):
        i0 = int(i * step * sample_rate)
        if i0 + n_click > len(sig):
            continue
        local_t = np.linspace(0, click_len, n_click, endpoint=False)
        if i % 2 == 0:
            env = np.exp(-30 * local_t)
            click = env * (np.sin(2 * np.pi * 80 * local_t) + 0.3 * rng.uniform(-1, 1, n_click))
        else:
            env = np.exp(-80 * local_t)
            click = env * rng.uniform(-1, 1, n_click)
        sig[i0 : i0 + n_click] += click
    return sig


def synth_pad_interference(
    duration_s: float, sample_rate: int, root_midi: int = 48, seed: int = 21
) -> np.ndarray:
    """A sustained, slowly-swelling background chord (root + major third +
    fifth) -- constant tonal presence throughout the clip, unlike the
    vocal/percussion interference's discrete events. Models "a pluck melody
    over a pad" (a common real-recording texture: lead instrument over a
    held synth/string bed)."""
    rng = np.random.default_rng(seed)
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    chord_intervals = [0, 4, 7]  # major triad
    sig = np.zeros_like(t)
    swell = 0.6 + 0.4 * np.sin(2 * np.pi * 0.15 * t)  # slow amplitude swell
    for interval in chord_intervals:
        freq = midi_to_freq(root_midi + interval)
        sig += np.sin(2 * np.pi * freq * t) + 0.4 * np.sin(2 * np.pi * freq * 2 * t)
    _ = rng  # reserved for future randomized variants; unused today
    return sig * swell


def _interference_for(kind: str, duration_s: float, sample_rate: int, seed: int) -> np.ndarray:
    if kind == "vocal_percussion":
        vocal = synth_vocal_interference(duration_s, sample_rate, seed=seed)
        perc = synth_percussion_interference(duration_s, sample_rate, seed=seed + 1)
        interference = vocal + perc
    elif kind == "pad":
        interference = synth_pad_interference(duration_s, sample_rate, seed=seed)
    else:
        raise ValueError(f"unknown interference_kind {kind!r}, expected one of {sorted(_INTERFERENCE_KINDS)}")

    peak = np.max(np.abs(interference))
    if peak > 0:
        interference = interference / peak
    return interference


def generate_mixed_clip(spec: MixedClipSpec, path: Path) -> ReferenceClip:
    """Renders `spec.base` (respecting its own `renderer` field -- synthetic
    or real-piano-sample, exactly like `test_fixtures.reference.
    generate_reference_clip` / `test_fixtures.real_piano.render_real_piano_clip`
    would on their own), mixes the requested interference layer on top at
    `spec.interference_gain`, and writes the result to `path`. Returns a
    ReferenceClip whose `events` are the base spec's instrument notes only
    -- interference never contributes ground-truth events."""
    clean_path = path.with_name(f"{path.stem}_clean{path.suffix}")

    if getattr(spec.base, "renderer", "synthetic") == "real_piano_sample":
        from test_fixtures.real_piano import render_real_piano_clip

        clip = render_real_piano_clip(spec.base, clean_path)
    else:
        from test_fixtures.reference import generate_reference_clip

        clip = generate_reference_clip(spec.base, clean_path)

    sample_rate, clean_pcm = wavfile.read(clip.path)
    clean = clean_pcm.astype(np.float64) / 32767.0
    duration_s = len(clean) / sample_rate

    interference = _interference_for(spec.interference_kind, duration_s, sample_rate, spec.seed)
    # Guard against a one-sample rounding mismatch between the clean
    # renderer's own sample-count math and this module's independent
    # `duration_s * sample_rate` computation -- pad or trim rather than
    # letting a broadcast error surface from a difference of one sample.
    if len(interference) < len(clean):
        interference = np.pad(interference, (0, len(clean) - len(interference)))
    elif len(interference) > len(clean):
        interference = interference[: len(clean)]
    mixed = clean + spec.interference_gain * interference

    peak = np.max(np.abs(mixed))
    if peak > 0:
        mixed = mixed / peak * 0.9
    pcm = (mixed * 32767).astype(np.int16)

    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, pcm)

    return ReferenceClip(path=path, events=clip.events, spec=clip.spec)
