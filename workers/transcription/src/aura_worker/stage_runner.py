from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from aura_api.models import StageArtifact, TranscriptionJob

STAGE_PROGRESS = {
    "probe": 10,
    "separate": 18,
    "normalize": 25,
    "inference": 55,
    "structure": 65,
    "quantize": 75,
    "assign": 85,
    "export": 100,
}


class StorageLike(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...


@dataclass
class StageContext:
    job: TranscriptionJob
    session: Session
    storage: StorageLike
    workdir: Path


def start_stage(ctx: StageContext, stage_name: str) -> None:
    """Marks `stage_name` as the job's CURRENT stage before it actually
    runs -- called from aura_worker.runner right before each stage's
    `run()`.

    Bug: previously job.stage/job.progress were only ever advanced by
    save_artifact, which a stage calls at the END of its own work (after
    it has already produced output). That means the row showed the LAST
    COMPLETED stage's name for the entire duration of the NEXT stage's
    run -- e.g. a piano job sat on "normalize" (job.progress == 25) for
    the whole multi-minute CRNN inference call, because inference.run only
    calls save_artifact("inference", ...) at the very end, right before
    returning. The user has no way to tell "genuinely stuck at normalize"
    apart from "actually deep into the slow inference stage" from the API
    response alone.

    Deliberately does NOT touch job.progress: by the time start_stage(X)
    runs, job.progress already holds whatever save_artifact set it to when
    the PREVIOUS stage that actually ran finished (or its initial 0, for
    the very first stage) -- exactly the "percent complete" figure that is
    still true while X is in flight. Recomputing it from a fixed pipeline
    position here would go wrong the moment a stage is conditionally
    skipped (the optional `separate` stage, guitar-only): a piano job's
    start_stage("normalize") would then have to "invent" a position for a
    separate stage that never runs for piano at all. Leaving progress
    alone sidesteps that entirely, and is still trivially monotonic
    non-decreasing across a whole job (start_stage never lowers it; only
    save_artifact ever raises it, to that completed stage's own
    STAGE_PROGRESS value).
    """
    ctx.job.stage = stage_name
    ctx.session.commit()


def find_cached_artifact(ctx: StageContext, stage_name: str, version: int) -> StageArtifact | None:
    return (
        ctx.session.query(StageArtifact)
        .filter_by(job_id=ctx.job.id, stage=stage_name, version=version)
        .one_or_none()
    )


def save_artifact(
    ctx: StageContext,
    stage_name: str,
    version: int,
    object_key: str,
    sha256: str,
    metrics: dict,
) -> StageArtifact:
    artifact = StageArtifact(
        job_id=ctx.job.id,
        stage=stage_name,
        version=version,
        object_key=object_key,
        sha256=sha256,
        metrics=metrics,
    )
    ctx.session.add(artifact)
    ctx.job.stage = stage_name
    ctx.job.progress = STAGE_PROGRESS.get(stage_name, ctx.job.progress)
    ctx.session.commit()
    return artifact
