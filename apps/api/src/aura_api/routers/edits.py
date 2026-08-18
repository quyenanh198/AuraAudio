from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from aura_api.deps import get_db
from aura_api.models import Project, ScoreRevision, TranscriptionJob
from aura_api.queue import enqueue_rederive_job
from aura_api.routers.scores import _latest_artifact
from aura_api.storage import storage_client
from score_schema.edits import EditError, apply_edit

router = APIRouter(tags=["edits"])


def _head_revision(db: Session, project: Project) -> ScoreRevision | None:
    head_id = (project.settings or {}).get("scoreHeadRevisionId")
    return db.get(ScoreRevision, head_id) if head_id else None


def _set_head(db: Session, project: Project, revision: ScoreRevision) -> None:
    settings = dict(project.settings or {})
    settings["scoreHeadRevisionId"] = revision.id
    project.settings = settings
    flag_modified(project, "settings")


def _load_baseline_score(db: Session, project: Project) -> dict:
    """Read-only: resolve the assign artifact's score without writing
    anything. Kept separate from `_insert_baseline_revision` so a project's
    very first edit can validate the op against this score BEFORE any
    ScoreRevision row is created — see `apply_project_edit`."""
    artifact = _latest_artifact(db, project.id, "assign")
    if artifact is None:
        raise HTTPException(status_code=404, detail="no transcribed score yet")
    return json.loads(storage_client.get_bytes(artifact.object_key))


def _insert_baseline_revision(db: Session, project: Project, score: dict) -> ScoreRevision:
    top = (
        db.query(ScoreRevision).filter(ScoreRevision.project_id == project.id)
        .order_by(ScoreRevision.revision.desc()).first()
    )
    baseline = ScoreRevision(
        project_id=project.id, parent_id=top.id if top else None,
        revision=(top.revision + 1) if top else 0,
        score_json=score, created_by="baseline",
    )
    db.add(baseline)
    db.flush()
    return baseline


def _start_rederive(db: Session, project: Project) -> TranscriptionJob:
    source = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.project_id == project.id)
        .order_by(TranscriptionJob.created_at.desc()).first()
    )
    job = TranscriptionJob(
        project_id=project.id, media_asset_id=source.media_asset_id,
        # Deviation from the brief's `f"rederive-{project_id}-{db.query(...).count()}"`
        # scheme (pre-approved): TranscriptionJob.input_hash carries a UNIQUE
        # constraint (uq_job_project_input_hash), and a global row count is
        # not collision-proof — it repeats after any job row is ever deleted,
        # racing two concurrent rederives, or restoring from a backup. A
        # per-call UUID suffix has no such failure mode.
        input_hash=f"rederive-{project.id}-{uuid.uuid4().hex[:12]}",
        status="queued", stage="rederive", progress=0,
    )
    db.add(job)
    db.flush()
    return job


def _respond(db: Session, project: Project, revision: ScoreRevision) -> dict:
    _set_head(db, project, revision)
    job = _start_rederive(db, project)
    db.commit()
    enqueue_rederive_job(job.id)
    return {"version": revision.revision, "score": revision.score_json,
            "rederive_job_id": job.id}


@router.post("/projects/{project_id}/edits")
def apply_project_edit(project_id: str, op: dict, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    head = _head_revision(db, project)
    base_score = head.score_json if head is not None else _load_baseline_score(db, project)
    try:
        edited = apply_edit(base_score, op)
    except EditError as exc:
        # No ScoreRevision row (baseline or otherwise) has been created yet
        # at this point — base_score came from either the existing head
        # (read-only) or `_load_baseline_score` (read-only, no db.add). The
        # invariant this depends on: an invalid op leaves zero new rows, no
        # head pointer, and no job row, without relying on the request
        # session's close-time implicit rollback.
        raise HTTPException(status_code=422, detail=exc.reason) from exc

    if head is None:
        head = _insert_baseline_revision(db, project, base_score)

    (
        db.query(ScoreRevision)
        .filter(ScoreRevision.project_id == project_id,
                ScoreRevision.revision > head.revision)
        .delete()
    )
    revision = ScoreRevision(
        project_id=project_id, parent_id=head.id, revision=head.revision + 1,
        score_json=edited, created_by="user",
    )
    db.add(revision)
    db.flush()
    return _respond(db, project, revision)


@router.post("/projects/{project_id}/edits/undo")
def undo_edit(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    head = _head_revision(db, project)
    if head is None or head.parent_id is None or head.created_by == "baseline":
        raise HTTPException(status_code=409, detail="nothing to undo")
    parent = db.get(ScoreRevision, head.parent_id)
    return _respond(db, project, parent)


@router.post("/projects/{project_id}/edits/redo")
def redo_edit(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    head = _head_revision(db, project)
    child = (
        db.query(ScoreRevision)
        .filter(ScoreRevision.parent_id == (head.id if head else None))
        .first()
    ) if head else None
    if child is None:
        raise HTTPException(status_code=409, detail="nothing to redo")
    return _respond(db, project, child)


@router.post("/projects/{project_id}/edits/revert")
def revert_edits(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    baseline = (
        db.query(ScoreRevision)
        .filter(ScoreRevision.project_id == project_id,
                ScoreRevision.created_by == "baseline")
        .order_by(ScoreRevision.revision.desc()).first()
    )
    if baseline is None:
        raise HTTPException(status_code=409, detail="no edits to revert")
    return _respond(db, project, baseline)
