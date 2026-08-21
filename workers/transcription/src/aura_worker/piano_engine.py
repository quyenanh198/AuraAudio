"""Piano-specific transcription engine: ByteDance/Kong's "High-resolution
Piano Transcription with Pedals" CRNN model
(https://github.com/bytedance/piano_transcription, PyPI package
`piano_transcription_inference`), used only for piano projects behind
`aura_worker.stages.inference`'s engine adapter -- guitar keeps basic-pitch
unchanged. See docs/superpowers/SESSION-HANDOFF.md's "Detection-quality
roadmap" item 2 and docs/benchmarks/2026-08-21-dq2.md for the full
candidate assessment, benchmark, and license record.

OFFLINE GUARANTEE: the model's own __init__ downloads its checkpoint from
Zenodo on first use *unless* a local `checkpoint_path` already exists at
the expected size -- this module always resolves and passes an explicit
local path (see `_resolve_checkpoint_path`), fetched at BUILD time by
scripts/fetch_piano_weights.py, so the network is never touched during a
real transcription request.

CONFIDENCE CAVEAT (read before touching ghost_filter for this engine):
unlike basic-pitch, `RegressionPostProcessor.output_dict_to_midi_events`
does not expose any per-note detection probability -- its final note
events carry only onset_time/offset_time/midi_note/velocity (verified
directly against piano_transcription_inference's utilities.py). Velocity
(the model's own 0-127 output) is used below as an ordering-preserving
*proxy* for NoteEvent.confidence, purely for UI display (Sidebar.svelte's
per-note confidence readout) -- it is NOT a calibrated detection
probability like basic-pitch's mean-frame-activation, and
`aura_worker.ghost_filter.filter_ghost_notes` is deliberately NOT applied
to this engine's output (see `transcribe_piano`'s docstring for why).
"""
from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from score_schema.models import NoteEvent

# Onset/offset/frame thresholds the upstream model exposes as plain
# instance attributes (piano_transcription_inference.inference.
# PianoTranscription sets its own defaults of 0.3/0.3/0.1 in __init__).
# Re-derived for THIS project's benchmark suite via a quick grid probe
# during DQ-2's investigation (docs/benchmarks/2026-08-21-dq2.md's
# "Threshold re-derivation" section): onset_threshold=0.7 was the clear
# best of {0.3, 0.5, 0.7, 0.9} on the 5-fixture piano cohort; frame
# threshold made no measurable difference in {0.1, 0.2} at that onset
# level, so it is left at the upstream default. offset_threshold is not
# used by this project (onset-only scoring, matching how basic-pitch's own
# offset_threshold is unused by aura_worker.eval.metrics.onset_f1) and is
# left at the upstream default.
ONSET_THRESHOLD = 0.7
OFFSET_THRESHOLD = 0.3
FRAME_THRESHOLD = 0.1

# The model's own required sample rate (piano_transcription_inference.
# config.sample_rate) -- verified directly, not assumed.
_MODEL_SAMPLE_RATE = 16000

_CHECKPOINT_ENV_VAR = "AURA_PIANO_CHECKPOINT_PATH"
_CHECKPOINT_FROZEN_SUBDIR = "piano_weights"
_CHECKPOINT_FILENAME = "piano_transcription_crnn.pth"

_model_lock = threading.Lock()
_model = None  # lazy-loaded singleton -- load is ~5-25s on CPU, do it once per worker process


class PianoWeightsMissingError(RuntimeError):
    """Raised when the piano checkpoint isn't present at any resolved
    location -- run scripts/fetch_piano_weights.py (dev) or verify
    build-backend.sh staged it (packaged app)."""


def _resolve_checkpoint_path() -> Path:
    import os

    env_override = os.environ.get(_CHECKPOINT_ENV_VAR)
    if env_override:
        return Path(env_override)

    # PyInstaller --onedir bundle: build-backend.sh stages the checkpoint
    # via --add-data into `<bundle_root>/piano_weights/`, mirroring how
    # basic_pitch's own weights land under the bundle root via
    # --collect-data (see build-backend.sh's comments for both).
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / _CHECKPOINT_FROZEN_SUBDIR / _CHECKPOINT_FILENAME  # type: ignore[attr-defined]

    # Dev / test / non-frozen worker: repo-relative, populated by
    # scripts/fetch_piano_weights.py. __file__ is
    # workers/transcription/src/aura_worker/piano_engine.py, so
    # parents[2] is workers/transcription/.
    repo_relative = Path(__file__).resolve().parents[2] / "weights" / "piano" / _CHECKPOINT_FILENAME
    return repo_relative


def _load_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is not None:  # re-check inside the lock
            return _model
        checkpoint_path = _resolve_checkpoint_path()
        if not checkpoint_path.exists():
            raise PianoWeightsMissingError(
                f"piano transcription checkpoint not found at {checkpoint_path}. "
                "Run `uv run --package aura-worker python "
                "workers/transcription/scripts/fetch_piano_weights.py` first "
                f"(or set {_CHECKPOINT_ENV_VAR})."
            )
        from piano_transcription_inference import PianoTranscription

        model = PianoTranscription(device="cpu", checkpoint_path=str(checkpoint_path))
        model.onset_threshold = ONSET_THRESHOLD
        model.offset_threshod = OFFSET_THRESHOLD  # upstream's own attribute name (sic)
        model.frame_threshold = FRAME_THRESHOLD
        _model = model
        return _model


@dataclass(frozen=True)
class _RawPianoNote:
    onset_s: float
    offset_s: float
    pitch: int
    velocity: int


def transcribe_piano(normalized_path: Path) -> list[NoteEvent]:
    """Runs the piano transcription CRNN model over `normalized_path` and
    returns NoteEvents in this project's shape.

    Does NOT apply aura_worker.ghost_filter.filter_ghost_notes: that
    filter's confidence floor and octave-shadow dedupe were tuned against
    basic-pitch's mean-frame-activation confidence distribution and a
    diagnosed harmonic-overtone artifact pattern specific to basic-pitch's
    architecture (see ghost_filter.py's own docstring). This model exposes
    no per-note confidence signal at all (see module docstring), so the
    confidence floor cannot be applied meaningfully, and no octave-shadow
    diagnosis has been run against this model's own output distribution --
    porting untuned constants across architectures would be exactly the
    "green-but-fake" outcome this integration must avoid. This model's own
    onset/offset/frame regression thresholds (tuned above) already serve
    the equivalent role of pruning low-confidence detections before they
    ever become note events.
    """
    import librosa

    model = _load_model()
    audio, _ = librosa.load(str(normalized_path), sr=_MODEL_SAMPLE_RATE, mono=True)
    # midi_path=None -- we only need est_note_events, no file write.
    output = model.transcribe(audio, None)

    raw_notes = [
        _RawPianoNote(
            onset_s=float(n["onset_time"]),
            offset_s=float(n["offset_time"]),
            pitch=int(n["midi_note"]),
            velocity=int(n["velocity"]),
        )
        for n in output["est_note_events"]
    ]

    return [
        NoteEvent(
            pitch=note.pitch,
            onset_s=note.onset_s,
            # Guard against a zero/negative-duration note.
            offset_s=max(note.offset_s, note.onset_s + 1e-3),
            velocity=max(0, min(127, note.velocity)),
            # Proxy confidence -- see module docstring's CONFIDENCE CAVEAT.
            confidence=max(0.0, min(1.0, note.velocity / 127.0)),
        )
        for note in raw_notes
    ]
