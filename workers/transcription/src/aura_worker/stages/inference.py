from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aura_worker.errors import JobFailure
from aura_worker.ghost_filter import filter_ghost_notes
from aura_worker.instrument_thresholds import thresholds_for_instrument
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import JobErrorCode, NoteEvent

# v1 -> v2 (detection-quality roadmap item 1): ghost-note filtering
# (aura_worker.ghost_filter) applied to raw predictions, and basic-pitch's
# onset/frame thresholds now vary per instrument
# (aura_worker.instrument_thresholds) instead of using basic-pitch's
# built-in defaults for every instrument. Both change this stage's output,
# so cached artifacts from v1 must not be reused.
STAGE_VERSION = 2


def run(ctx: StageContext, normalized_path: Path) -> list[NoteEvent]:
    cached = find_cached_artifact(ctx, "inference", STAGE_VERSION)
    if cached is not None:
        raw = json.loads(ctx.storage.get_bytes(cached.object_key))
        return [NoteEvent(**item) for item in raw]

    thresholds = thresholds_for_instrument(ctx.job.project.instrument)

    try:
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH

        _, _, note_events = predict(
            str(normalized_path),
            model_or_model_path=ICASSP_2022_MODEL_PATH,
            onset_threshold=thresholds.onset_threshold,
            frame_threshold=thresholds.frame_threshold,
        )
    except JobFailure:
        raise
    except Exception as exc:  # basic-pitch/tensorflow errors are not a stable type to catch narrowly
        raise JobFailure(JobErrorCode.MODEL_FAILED, f"inference failed: {exc}") from exc

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
    notes = filter_ghost_notes(raw_notes)

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
