"""Opt-in source separation: isolates the target instrument from a mixed
recording (vocals/drums/bass/other instruments) before inference, using
Meta's Demucs (`demucs` PyPI package, MIT-licensed code;
`htdemucs_6s` pretrained weights). Detection-quality roadmap item 3 --
see docs/superpowers/SESSION-HANDOFF.md's "Detection-quality roadmap" and
docs/benchmarks/2026-08-21-dq3.md for the full candidate assessment,
stem-mapping decision, and benchmark evidence.

GUITAR ONLY, evidence-backed: dq3.md's mixed-fixture benchmark found
`htdemucs_6s`'s own dedicated "piano" stem is unreliable for piano (mean
F1 below the unseparated mix across repeated runs, consistent with
upstream's own documented caveat that the 6-source model's piano stem
"doesn't work so well"). This module is therefore only ever called for
guitar projects -- see `aura_worker.stages.separate`'s instrument gate,
not a check duplicated here.

STEM MAPPING, evidence-revised: sums the "guitar" AND "other" stems, not
either alone. A first pass targeting "other" alone looked like a clean win
on a vocals+percussion mixed fixture (0.800 -> 1.000 onset F1) but a real,
committed benchmark run surfaced a second, contradictory fixture: when the
only non-guitar content is a sustained pad chord (no drums/vocals-like
signal for the model to route elsewhere), htdemucs_6s puts almost ALL of
the real guitar signal into its OWN "guitar" stem instead -- measured
directly: "other" stem RMS 0.052 (indistinguishable from the ~0.05 noise
floor shared by drums/bass/vocals/piano on that fixture), "guitar" stem
RMS 0.170 (by far the loudest). "Other" alone gave 0 detected notes on
that fixture (a total failure, not just a regression), and clean guitar
recordings with NO interference at all showed the identical "which stem
did the model choose" ambiguity -- 3 of 7 clean fixtures regressed, one by
-0.381 F1. Summing both stems recovers the full guitar signal regardless
of which stem the model routed it to, at the cost of also including
whatever (typically much quieter, per the RMS numbers above) leakage lands
in the other stem. Verified this resolves both failure modes without
losing the original vocals+percussion win -- see dq3.md's "Stem-mapping
decision" section for the full before/after table.

OFFLINE GUARANTEE: `demucs.pretrained.get_model(name, repo=<local dir>)`
skips its default HuggingFace-hub download entirely whenever `repo` is not
None (verified directly against demucs/pretrained.py) -- this module always
passes an explicit local weights directory (fetched at BUILD time by
scripts/fetch_demucs_weights.py), so the network is never touched during a
real transcription request.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np

_WEIGHTS_ENV_VAR = "AURA_DEMUCS_WEIGHTS_DIR"
_WEIGHTS_FROZEN_SUBDIR = "demucs_weights"
_MODEL_NAME = "htdemucs_6s"
# See module docstring's "STEM MAPPING" paragraph for why both, summed,
# not either alone.
_TARGET_STEMS = ("guitar", "other")

_model_lock = threading.Lock()
_model = None  # lazy-loaded singleton -- load is a few seconds, do it once per worker process


class DemucsWeightsMissingError(RuntimeError):
    """Raised when the demucs weights directory isn't present at any
    resolved location -- run scripts/fetch_demucs_weights.py (dev) or
    verify build-backend.sh staged it (packaged app)."""


def _resolve_weights_dir() -> Path:
    import os

    env_override = os.environ.get(_WEIGHTS_ENV_VAR)
    if env_override:
        return Path(env_override)

    # PyInstaller --onedir bundle: build-backend.sh stages the weights
    # directory via --add-data into `<bundle_root>/demucs_weights/`,
    # mirroring aura_worker.piano_engine's identical frozen-mode pattern.
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / _WEIGHTS_FROZEN_SUBDIR  # type: ignore[attr-defined]

    # Dev / test / non-frozen worker: repo-relative, populated by
    # scripts/fetch_demucs_weights.py. __file__ is
    # workers/transcription/src/aura_worker/separation.py, so parents[2]
    # is workers/transcription/.
    return Path(__file__).resolve().parents[2] / "weights" / "demucs"


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:  # re-check inside the lock
            return _model
        weights_dir = _resolve_weights_dir()
        manifest = weights_dir / f"{_MODEL_NAME}.yaml"
        if not manifest.exists():
            raise DemucsWeightsMissingError(
                f"demucs weights not found at {weights_dir}. "
                "Run `uv run --package aura-worker python "
                "workers/transcription/scripts/fetch_demucs_weights.py` first "
                f"(or set {_WEIGHTS_ENV_VAR})."
            )
        from demucs.pretrained import get_model

        model = get_model(_MODEL_NAME, repo=weights_dir)
        model.eval()
        _model = model
        return _model


def separate_guitar(source_path: Path, out_path: Path) -> Path:
    """Runs demucs over `source_path` (any container/codec ffmpeg can
    decode -- demucs.audio.AudioFile shells out to ffmpeg itself) and
    writes the isolated guitar signal (the "guitar" and "other" stems
    summed -- see module docstring's "STEM MAPPING" paragraph for why both)
    to `out_path` as a mono WAV at the model's native sample rate. Returns
    `out_path`.

    DETERMINISM: `apply_model`'s own `shifts` parameter defaults to `1`,
    not `0` -- undocumented in demucs's own docstring, found by direct
    measurement, not upstream's docs. A `shifts=1` (or higher) call applies
    a genuinely RANDOM time-shift test-time-augmentation trick each
    invocation (shift the input, run the model, shift back), not just
    floating-point non-associativity noise: two back-to-back calls on the
    IDENTICAL decoded tensor differed by 0.24 in raw stem amplitude (14% of
    the stem's own peak) with the default. This surfaced as unreproducible
    benchmark deltas in a code review of the first version of this module
    -- see docs/benchmarks/2026-08-21-dq3.md's "Determinism" section for
    the full before/after. Fixed by passing `shifts=0` explicitly below,
    which disables that augmentation entirely: verified via a real,
    isolated test (identical decoded input tensor, `torch.equal` on the
    raw output) that this gives bit-for-bit identical results run to run,
    IN-PROCESS and ACROSS SEPARATE PROCESS INVOCATIONS, at the DEFAULT
    thread count -- no thread-pinning needed, and thread-pinning alone
    (without `shifts=0`) does NOT fix it, since the random shift is the
    actual root cause, not a parallel-reduction ordering effect.
    `torch.manual_seed` is not required either for the same reason (there
    is no remaining randomness to seed once `shifts=0` removes the only
    random operation in this code path).

    Deliberately downmixes to mono here (rather than preserving stereo):
    every downstream stage (normalize.py) downmixes to mono anyway, and
    demucs's own separation quality depends on the STEREO input it
    receives (see aura_worker.stages.separate's placement in the pipeline,
    before normalize's mono downmix) -- not on what this function returns.
    """
    import torch
    from demucs.apply import apply_model
    from demucs.audio import AudioFile
    from scipy.io import wavfile

    model = _load_model()
    wav = AudioFile(str(source_path)).read(
        streams=0, samplerate=model.samplerate, channels=model.audio_channels
    )
    ref = wav.mean(0)
    ref_std = ref.std()
    if ref_std == 0:
        # Degenerate (silent) input -- nothing to separate; avoid a divide
        # by zero and just pass the (silent) input through as the stem.
        ref_std = torch.tensor(1.0)
    normalized = (wav - ref.mean()) / ref_std

    with torch.no_grad():
        sources = apply_model(
            model, normalized[None], device="cpu", progress=False, split=True, overlap=0.25,
            shifts=0,  # disables random shift augmentation -- see docstring's DETERMINISM paragraph
        )[0]
    sources = sources * ref_std + ref.mean()

    stem = sum(sources[model.sources.index(name)].mean(0) for name in _TARGET_STEMS).numpy()

    peak = float(np.max(np.abs(stem))) or 1.0
    pcm = np.clip(stem / peak * 0.9, -1.0, 1.0)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(out_path), model.samplerate, (pcm * 32767).astype(np.int16))
    return out_path
