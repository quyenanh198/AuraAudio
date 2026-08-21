from __future__ import annotations

import uuid

from pydantic import BaseModel, Field, field_validator

_ALLOWED_CONTENT_TYPES = {
    "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4",
    "video/mp4", "video/quicktime",
}


class CreateUploadResponse(BaseModel):
    object_key: str


def make_upload_object_key(filename: str) -> str:
    return f"uploads/{uuid.uuid4()}/{filename}"


class CreateProjectRequest(BaseModel):
    title: str
    instrument: str

    @field_validator("instrument")
    @classmethod
    def instrument_supported(cls, v: str) -> str:
        if v not in {"guitar", "piano"}:
            raise ValueError("instrument must be 'guitar' or 'piano'")
        return v

    object_key: str
    # Detection-quality roadmap item 3: opt-in "isolate instrument from
    # mix" source-separation step before inference, stored on
    # Project.settings (no DB migration needed) and read by
    # POST .../transcriptions. Guitar only in practice -- the frontend
    # only offers the toggle for guitar (see docs/benchmarks/
    # 2026-08-21-dq3.md's benchmark evidence for why); a piano project
    # that sets this to true is not an error, it is simply a no-op (see
    # aura_worker.runner's call site), never default True.
    separate_source: bool = False


class ProjectResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    title: str
    instrument: str
    media_asset_id: str
    separate_source: bool


class CreateJobResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    id: str
    status: str
    stage: str | None
    progress: int
    error_code: str | None
    error_detail: str | None


class ExportStatusResponse(BaseModel):
    id: str
    format: str
    status: str
    download_url: str | None


class ProjectJobSummary(BaseModel):
    id: str
    status: str
    stage: str | None
    progress: int


class ProjectExportSummary(BaseModel):
    id: str
    format: str


class ProjectListItem(BaseModel):
    id: str
    title: str
    instrument: str
    created_at: str
    duration_ms: int | None
    job: ProjectJobSummary | None
    exports: list[ProjectExportSummary]


class DependencyStatus(BaseModel):
    found: bool
    version: str | None


class SystemDepsResponse(BaseModel):
    ffmpeg: DependencyStatus
    ffprobe: DependencyStatus
    # Optional dependency, guided-install (not bundled, not a deb Depends).
    # Deliberately excluded from `allFound` — see routers/system.py.
    ytDlp: DependencyStatus
    allFound: bool


class ImportYoutubeRequest(BaseModel):
    # 2048 is a generous, conventional URL-length ceiling (well past any
    # real YouTube watch/share URL) -- just a sanity bound against
    # pathological input reaching the yt-dlp subprocess, not a functional
    # requirement.
    url: str = Field(max_length=2048)


class ImportYoutubeResponse(BaseModel):
    """Shape-compatible with `CreateUploadResponse`: always carries
    `object_key`, so the frontend can feed it into the same
    create-project flow it already uses after a file upload. `title`
    is additive and optional (best-effort, from yt-dlp's own printed
    metadata — omitted rather than guessed at when unavailable)."""

    object_key: str
    title: str | None = None
