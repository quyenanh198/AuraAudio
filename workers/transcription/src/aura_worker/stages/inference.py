from __future__ import annotations

import hashlib
import json
from pathlib import Path

from score_schema.models import JobErrorCode, NoteEvent

from aura_worker.errors import JobFailure
from aura_worker.ghost_filter import filter_ghost_notes
from aura_worker.instrument_thresholds import thresholds_for_instrument
from aura_worker.piano_engine import transcribe_piano
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact

# v1 -> v2 (detection-quality roadmap item 1): ghost-note filtering
# (aura_worker.ghost_filter) applied to raw predictions, and basic-pitch's
# onset/frame thresholds now vary per instrument
# (aura_worker.instrument_thresholds) instead of using basic-pitch's
# built-in defaults for every instrument.
# v2 -> v3 (detection-quality roadmap item 2): piano projects now run a
# dedicated engine (aura_worker.piano_engine, ByteDance/Kong's piano
# transcription CRNN) instead of basic-pitch -- see
# docs/benchmarks/2026-08-21-dq2.md for the full candidate assessment and
# benchmark. Guitar's code path below is untouched byte-for-byte (still
# basic-pitch, same thresholds, same ghost filter); this version bump only
# exists because this stage's shared cache key (stage name + version, not
# per-instrument) can't distinguish "piano's algorithm changed" from
# "guitar's did too" -- bumping it forces one harmless guitar cache miss
# that recomputes the same, byte-identical basic-pitch output.
STAGE_VERSION = 3


def run(ctx: StageContext, normalized_path: Path) -> list[NoteEvent]:
    cached = find_cached_artifact(ctx, "inference", STAGE_VERSION)
    if cached is not None:
        raw = json.loads(ctx.storage.get_bytes(cached.object_key))
        return [NoteEvent(**item) for item in raw]

    instrument = ctx.job.project.instrument

    try:
        if instrument == "piano":
            notes = transcribe_piano(normalized_path)
        else:
            notes = _run_basic_pitch(normalized_path, instrument)
    except JobFailure:
        raise
    except Exception as exc:  # model/inference errors are not a stable type to catch narrowly
        raise JobFailure(JobErrorCode.MODEL_FAILED, f"inference failed: {exc}") from exc

    if not notes:
        raise JobFailure(JobErrorCode.NO_MUSIC_DETECTED, "model returned zero note events")

    key = f"jobs/{ctx.job.id}/stage/notes.json"
    payload = json.dumps([n.__dict__ for n in notes]).encode()
    ctx.storage.put_bytes(key, payload)
    save_artifact(
        ctx, "inference", STAGE_VERSION, object_key=key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={"note_count": len(notes)},
    )
    return notes


def _run_basic_pitch(normalized_path: Path, instrument: str) -> list[NoteEvent]:
    """basic-pitch inference path -- guitar (and any instrument other than
    "piano") only. Byte-for-byte the same logic as before DQ-2: same
    per-instrument thresholds (aura_worker.instrument_thresholds), same
    ghost-note filter (aura_worker.ghost_filter), just extracted into its
    own function so `run()` above can route piano to
    aura_worker.piano_engine instead."""
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict

    thresholds = thresholds_for_instrument(instrument)
    _, _, note_events = predict(
        str(normalized_path),
        model_or_model_path=ICASSP_2022_MODEL_PATH,
        onset_threshold=thresholds.onset_threshold,
        frame_threshold=thresholds.frame_threshold,
    )

    # note_events entries are (start_time_s, end_time_s, pitch_midi, amplitude, pitch_bends);
    # pitch_midi is already a MIDI note number.
    raw_notes = [
        NoteEvent(
            pitch=int(round(pitch_midi)),
            onset_s=float(start_s),
            offset_s=float(end_s),
            velocity=int(round(min(max(amplitude, 0.0), 1.0) * 127)),
            confidence=float(min(max(amplitude, 0.0), 1.0)),
        )
        for start_s, end_s, pitch_midi, amplitude, *_rest in note_events
    ]
    return filter_ghost_notes(raw_notes)
