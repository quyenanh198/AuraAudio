# workers/transcription/src/aura_worker/stages/assign.py
from __future__ import annotations

import hashlib
import json

from aura_worker.fingering import assign_measure as assign_string_fret
from aura_worker.piano_hands import assign_measure as assign_hands
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.validate import validate_score

STAGE_VERSION = 2


def run(ctx: StageContext, score: dict) -> dict:
    cached = find_cached_artifact(ctx, "assign", STAGE_VERSION)
    if cached is not None:
        return json.loads(ctx.storage.get_bytes(cached.object_key))

    part = score["parts"][0]
    instrument = part["instrument"]
    for measure in part["measures"]:
        events = measure["events"]

        string_fret_assignments = assign_string_fret(events) if instrument == "guitar" else {}
        hand_assignments = assign_hands(events) if instrument == "piano" else {}

        for i, event in enumerate(events):
            sf = string_fret_assignments.get(i)
            event["string"] = sf.string if sf is not None else None
            event["fret"] = sf.fret if sf is not None else None
            event["hand"] = hand_assignments.get(i)

    validate_score(score)

    object_key = f"jobs/{ctx.job.id}/stage/assign.json"
    payload = json.dumps(score).encode()
    ctx.storage.put_bytes(object_key, payload)
    save_artifact(
        ctx, "assign", STAGE_VERSION, object_key=object_key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={},
    )
    return score
