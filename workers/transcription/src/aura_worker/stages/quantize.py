from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import NoteEvent, build_score
from score_schema.validate import validate_score

STAGE_VERSION = 1
BPM = 120
SECONDS_PER_BEAT = 60.0 / BPM
BEATS_PER_MEASURE = 4
GRID_BEATS = Fraction(1, 4)  # snap to 16th notes (1/4 of a beat, since a beat = quarter note)


def _seconds_to_beats(seconds: float) -> Fraction:
    raw_beats = Fraction(seconds / SECONDS_PER_BEAT).limit_denominator(64)
    return round(raw_beats / GRID_BEATS) * GRID_BEATS


def _beats_to_notated_fraction(beats: Fraction) -> str:
    """Notated duration/onset as a fraction of a whole note (4 beats)."""
    whole_note_fraction = beats / 4
    return f"{whole_note_fraction.numerator}/{whole_note_fraction.denominator}"


def run(ctx: StageContext, notes: list[NoteEvent]) -> dict:
    cached = find_cached_artifact(ctx, "quantize", STAGE_VERSION)
    if cached is not None:
        return json.loads(ctx.storage.get_bytes(cached.object_key))

    measures: dict[int, list[dict]] = {}

    for i, note in enumerate(notes):
        onset_beats = _seconds_to_beats(note.onset_s)
        offset_beats = _seconds_to_beats(note.offset_s)
        duration_beats = max(offset_beats - onset_beats, GRID_BEATS)

        measure_number = int(onset_beats // BEATS_PER_MEASURE) + 1
        onset_within_measure = onset_beats - (measure_number - 1) * BEATS_PER_MEASURE

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
        {"beat": 1, "seconds": SECONDS_PER_BEAT},
    ]

    score = build_score(instrument=ctx.job.project.instrument, time_map=time_map, measures=measure_list)
    validate_score(score)

    key = f"jobs/{ctx.job.id}/stage/score.json"
    payload = json.dumps(score).encode()
    ctx.storage.put_bytes(key, payload)

    from aura_api.models import ScoreRevision

    revision = ScoreRevision(
        project_id=ctx.job.project_id, parent_id=None, revision=0,
        score_json=score, created_by="system",
    )
    ctx.session.add(revision)
    save_artifact(
        ctx, "quantize", STAGE_VERSION, object_key=key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={"measure_count": len(measure_list)},
    )

    return score
