from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aura_api.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    instrument: Mapped[str] = mapped_column(String(16))  # "guitar" | "piano"
    tuning: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    media_assets: Mapped[list["MediaAsset"]] = relationship(back_populates="project")
    jobs: Mapped[list["TranscriptionJob"]] = relationship(back_populates="project")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(16))  # "source"
    object_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes: Mapped[int | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(nullable=True)

    project: Mapped[Project] = relationship(back_populates="media_assets")


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "input_hash", name="uq_job_project_input_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    media_asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"))
    status: Mapped[str] = mapped_column(String(16), default="created")
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress: Mapped[int] = mapped_column(default=0)
    input_hash: Mapped[str] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(16), default="v1")
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="jobs")
    artifacts: Mapped[list["StageArtifact"]] = relationship(back_populates="job")
    exports: Mapped[list["Export"]] = relationship(back_populates="job")


class StageArtifact(Base):
    __tablename__ = "stage_artifacts"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", "version", name="uq_artifact_job_stage_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id"))
    stage: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(default=1)
    object_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    job: Mapped[TranscriptionJob] = relationship(back_populates="artifacts")


class ScoreRevision(Base):
    __tablename__ = "score_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("score_revisions.id"), nullable=True)
    revision: Mapped[int] = mapped_column(default=0)
    score_json: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id"))
    revision: Mapped[int] = mapped_column(default=0)
    format: Mapped[str] = mapped_column(String(16))  # "midi" | "musicxml"
    status: Mapped[str] = mapped_column(String(16), default="pending")
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    job: Mapped[TranscriptionJob] = relationship(back_populates="exports")
