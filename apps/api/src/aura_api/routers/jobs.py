from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.hashing import compute_input_hash
from aura_api.models import MediaAsset, Project, TranscriptionJob
from aura_api.queue import enqueue_transcription_job
from aura_api.schemas import CreateJobResponse, JobStatusResponse

router = APIRouter(tags=["jobs"])


@router.post("/projects/{project_id}/transcriptions", response_model=CreateJobResponse)
def create_transcription(
    project_id: str, response: Response, db: Session = Depends(get_db)
) -> CreateJobResponse:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    asset = (
        db.query(MediaAsset)
        .filter(MediaAsset.project_id == project_id, MediaAsset.kind == "source")
        .order_by(MediaAsset.id.desc())
        .first()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="no source media for project")

    input_hash = compute_input_hash(
        object_key=asset.object_key,
        instrument=project.instrument,
        pipeline_version="v1",
    )

    existing = (
        db.query(TranscriptionJob)
        .filter(
            TranscriptionJob.project_id == project_id,
            TranscriptionJob.input_hash == input_hash,
        )
        .one_or_none()
    )
    if existing is not None:
        response.status_code = 200
        return CreateJobResponse(job_id=existing.id, status=existing.status)

    job = TranscriptionJob(
        project_id=project_id, media_asset_id=asset.id, input_hash=input_hash, status="queued"
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(TranscriptionJob)
            .filter(TranscriptionJob.project_id == project_id, TranscriptionJob.input_hash == input_hash)
            .one()
        )
        response.status_code = 200
        return CreateJobResponse(job_id=existing.id, status=existing.status)

    enqueue_transcription_job(job.id)
    response.status_code = 201
    return CreateJobResponse(job_id=job.id, status=job.status)


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    job = db.get(TranscriptionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(
        id=job.id, status=job.status, stage=job.stage, progress=job.progress,
        error_code=job.error_code, error_detail=job.error_detail,
    )
