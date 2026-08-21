"""Runs the real transcription stages in-process against an arbitrary WAV
file, for benchmark scoring — no HTTP, no job queue, no FastAPI TestClient.

Mirrors exactly how workers/transcription/tests/test_structure.py,
test_quantize.py, etc. invoke each stage directly: a FakeStorage dict
standing in for StorageLike, and a StageContext wrapping a real (but
scratch) SQLAlchemy session — because normalize.run/quantize.run/etc. read
and write real ORM rows (StageArtifact caching, ScoreRevision, Project
relationships), not just plain data.

Only normalize -> inference -> structure -> quantize run here (per the
detection-quality benchmark's scope — see docs/superpowers/SESSION-HANDOFF.md's
"Detection-quality roadmap" item 0): probe.run is skipped since it exists
only to download+validate an uploaded MediaAsset from storage, and this
harness already has a local WAV path in hand; assign/export are skipped
since fingering/staff placement and file export are not part of what this
benchmark measures.

Every aura_api / aura_worker.stages import below is deferred into the
function body (not module-level) so importing this module never triggers
aura_api.db's module-level `engine = get_engine()` before the caller has
had a chance to point DATABASE_URL/AURA_DATA_DIR at a scratch location —
see benchmark.py's top-of-file env var setup, which mirrors
workers/transcription/tests/conftest.py's same guard.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from score_schema.models import NoteEvent


class _DictStorage:
    """Minimal StorageLike (put_bytes/get_bytes) for in-process stage
    execution — the same FakeStorage shape used throughout
    workers/transcription/tests (e.g. test_structure.py, test_quantize.py)."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put_bytes(self, key: str, data: bytes) -> None:
        self._objects[key] = data

    def get_bytes(self, key: str) -> bytes:
        return self._objects[key]


@dataclass(frozen=True)
class PipelineResult:
    notes: list[NoteEvent]  # raw, performed-time predictions from inference.run
    tempo_bpm: float
    tempo_confidence: float
    meter: str
    meter_confidence: float
    key: str
    key_confidence: float
    score: dict  # the quantized schemaVersion-4 score JSON


def run_pipeline_stages(wav_path: Path, instrument: str, workdir: Path) -> PipelineResult:
    """Runs normalize -> inference -> structure -> quantize against
    `wav_path` and returns the raw + detected results needed for scoring.

    Caller must already have DATABASE_URL / AURA_DATA_DIR pointed at a
    scratch location (see module docstring) before the first call —
    subsequent calls reuse whatever engine that establishes.
    """
    import os

    from aura_api.db import Base
    from aura_api.models import MediaAsset, Project, TranscriptionJob
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from aura_worker.stage_runner import StageContext
    from aura_worker.stages import inference, normalize, quantize, structure

    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    try:
        project = Project(owner_id="benchmark", title=wav_path.stem, instrument=instrument)
        session.add(project)
        session.flush()
        asset = MediaAsset(
            project_id=project.id, kind="source", object_key=f"benchmark/{wav_path.name}"
        )
        session.add(asset)
        session.flush()
        job = TranscriptionJob(
            project_id=project.id,
            media_asset_id=asset.id,
            input_hash=wav_path.stem,
            status="queued",
        )
        session.add(job)
        session.commit()

        ctx = StageContext(job=job, session=session, storage=_DictStorage(), workdir=workdir)

        normalized_path = normalize.run(ctx, source_path=wav_path)
        notes = inference.run(ctx, normalized_path=normalized_path)
        structure_result = structure.run(ctx, normalized_path=normalized_path, notes=notes)
        score = quantize.run(ctx, notes, structure_result)

        return PipelineResult(
            notes=notes,
            tempo_bpm=structure_result.tempo_bpm,
            tempo_confidence=structure_result.tempo_confidence,
            meter=structure_result.meter,
            meter_confidence=structure_result.meter_confidence,
            key=structure_result.key,
            key_confidence=structure_result.key_confidence,
            score=score,
        )
    finally:
        session.close()
