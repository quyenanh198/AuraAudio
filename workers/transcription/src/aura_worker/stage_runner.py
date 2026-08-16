from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from aura_api.models import StageArtifact, TranscriptionJob

STAGE_PROGRESS = {
    "probe": 10,
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
