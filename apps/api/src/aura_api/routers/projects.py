from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import Export, MediaAsset, Project, TranscriptionJob
from aura_api.schemas import (
    CreateProjectRequest,
    ProjectExportSummary,
    ProjectJobSummary,
    ProjectListItem,
    ProjectResponse,
)
from aura_api.storage import storage_client

router = APIRouter(tags=["projects"])


@router.get("/projects", response_model=list[ProjectListItem])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectListItem]:
    projects = (
        db.query(Project).filter(Project.deleted_at.is_(None))
        .order_by(Project.created_at.desc()).all()
    )
    items: list[ProjectListItem] = []
    for p in projects:
        asset = (
            db.query(MediaAsset)
            .filter(MediaAsset.project_id == p.id, MediaAsset.kind == "source")
            .order_by(MediaAsset.id.desc()).first()
        )
        job = (
            db.query(TranscriptionJob)
            .filter(TranscriptionJob.project_id == p.id)
            .order_by(TranscriptionJob.created_at.desc()).first()
        )
        # Query exports by PROJECT, not by the latest job's id. An edit
        # (apply/undo/redo/revert) enqueues a NEW "rederive" TranscriptionJob
        # row that immediately becomes the "latest job" for the project, but
        # the rederive worker updates the project's EXISTING Export rows in
        # place (object_key/status/revision) rather than minting new ones
        # tied to the rederive job's id — see
        # workers/transcription/src/aura_worker/rederive.py::run_rederive_job
        # (`for export in session.query(Export).filter(Export.project_id ==
        # project.id)...`). Filtering by `job.id` here made the exports list
        # go permanently empty after a project's first edit, since Export
        # rows never carry a rederive job's id. Filtering by project_id
        # instead is correct for both the original export-stage creation
        # path (workers/.../stages/export.py always sets project_id) and
        # every subsequent rederive. A never-transcribed project still shows
        # no exports, since no Export row exists at all until the export
        # stage runs — this doesn't depend on `job` being present or
        # succeeded.
        exports = [
            ProjectExportSummary(id=e.id, format=e.format)
            for e in db.query(Export)
            .filter(Export.project_id == p.id, Export.status == "succeeded")
            .all()
        ]
        items.append(ProjectListItem(
            id=p.id, title=p.title, instrument=p.instrument,
            created_at=p.created_at.isoformat(),
            duration_ms=asset.duration_ms if asset else None,
            job=ProjectJobSummary(id=job.id, status=job.status, stage=job.stage, progress=job.progress) if job else None,
            exports=exports,
        ))
    return items


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(body: CreateProjectRequest, db: Session = Depends(get_db)) -> ProjectResponse:
    try:
        head = storage_client.head_object(body.object_key)
    except ValueError:
        # object_key resolved outside the storage root (path traversal
        # attempt) — treat identically to "not found", not a 500.
        raise HTTPException(status_code=404, detail="uploaded object not found") from None
    if head is None:
        raise HTTPException(status_code=404, detail="uploaded object not found")

    project = Project(
        owner_id="anonymous",
        title=body.title,
        instrument=body.instrument,
        # Detection-quality roadmap item 3's opt-in setting -- see
        # aura_api.schemas.CreateProjectRequest.separate_source's docstring.
        # Stored under Project.settings (existing JSON column, no
        # migration) rather than a new dedicated column.
        settings={"separateSource": body.separate_source},
    )
    db.add(project)
    db.flush()

    asset = MediaAsset(
        project_id=project.id,
        kind="source",
        object_key=body.object_key,
        bytes=head.get("ContentLength"),
    )
    db.add(asset)
    db.commit()

    return ProjectResponse(
        id=project.id,
        title=project.title,
        instrument=project.instrument,
        media_asset_id=asset.id,
        separate_source=body.separate_source,
    )
