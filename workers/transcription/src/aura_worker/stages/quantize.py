# workers/transcription/src/aura_worker/stages/quantize.py — full replacement
from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from aura_worker.stages.structure import METER_CANDIDATES, StructureResult
from score_schema.models import NoteEvent, build_score
from score_schema.validate import validate_score

STAGE_VERSION = 2
GRID_BEATS = Fraction(1, 4)  # snap to 16th notes (1/4 of a beat, since a beat = quarter note)


def _seconds_to_beats(seconds: float, seconds_per_beat: float) -> Fraction:
    raw_beats = Fraction(seconds / seconds_per_beat).limit_denominator(64)
    return round(raw_beats / GRID_BEATS) * GRID_BEATS


def _beats_to_notated_fraction(beats: Fraction) -> str:
    """Notated duration/onset as a fraction of a whole note (4 beats)."""
    whole_note_fraction = beats / 4
    return f"{whole_note_fraction.numerator}/{whole_note_fraction.denominator}"


def run(ctx: StageContext, notes: list[NoteEvent], structure: StructureResult) -> dict:
    cached = find_cached_artifact(ctx, "quantize", STAGE_VERSION)
    if cached is not None:
        return json.loads(ctx.storage.get_bytes(cached.object_key))

    seconds_per_beat = 60.0 / structure.tempo_bpm
    beats_per_measure = METER_CANDIDATES[structure.meter]

    measures: dict[int, list[dict]] = {}

    for i, note in enumerate(notes):
        onset_beats = _seconds_to_beats(note.onset_s, seconds_per_beat)
        offset_beats = _seconds_to_beats(note.offset_s, seconds_per_beat)
        duration_beats = max(offset_beats - onset_beats, GRID_BEATS)

        measure_number = int(onset_beats // beats_per_measure) + 1
        onset_within_measure = onset_beats - (measure_number - 1) * beats_per_measure

        event = {
            "id": f"note_{i:02d}",
            "pitch": note.pitch,
            "onsetSeconds": note.onset_s,
            "offsetSeconds": note.offset_s,
            "notatedOnset": _beats_to_notated_fraction(onset_within_measure),
            "notatedDuration": _beats_to_notated_fraction(duration_beats),
            "voice": 1,
            "confidence": note.confidence,
            "locked": False,
        }
        measures.setdefault(measure_number, []).append(event)

    measure_list = [
        {"number": number, "events": events}
        for number, events in sorted(measures.items())
    ]
    time_map = [
        {"beat": 0, "seconds": 0.0},
        {"beat": 1, "seconds": seconds_per_beat},
    ]

    score = build_score(
        instrument=ctx.job.project.instrument,
        tempo_bpm=structure.tempo_bpm,
        meter=structure.meter,
        key=structure.key,
        confidence={
            "tempo": structure.tempo_confidence,
            "meter": structure.meter_confidence,
            "key": structure.key_confidence,
        },
        time_map=time_map,
        measures=measure_list,
    )
    validate_score(score)

    object_key = f"jobs/{ctx.job.id}/stage/score.json"
    payload = json.dumps(score).encode()
    ctx.storage.put_bytes(object_key, payload)

    from aura_api.models import ScoreRevision

    revision = ScoreRevision(
        project_id=ctx.job.project_id, parent_id=None, revision=0,
        score_json=score, created_by="system",
    )
    ctx.session.add(revision)
    save_artifact(
        ctx, "quantize", STAGE_VERSION, object_key=object_key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={"measure_count": len(measure_list)},
    )

    return score
