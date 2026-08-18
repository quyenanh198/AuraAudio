import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import Project, StageArtifact, TranscriptionJob
from aura_api.storage import storage_client

router = APIRouter(tags=["scores"])


def _latest_artifact(db: Session, project_id: str, stage: str) -> StageArtifact | None:
    job = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.project_id == project_id, TranscriptionJob.status == "succeeded")
        .order_by(TranscriptionJob.created_at.desc()).first()
    )
    if job is None:
        return None
    return (
        db.query(StageArtifact)
        .filter(StageArtifact.job_id == job.id, StageArtifact.stage == stage)
        .order_by(StageArtifact.version.desc()).first()
    )


@router.get("/projects/{project_id}/score")
def get_score(project_id: str, db: Session = Depends(get_db)) -> dict:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    artifact = _latest_artifact(db, project_id, "assign")
    if artifact is None:
        raise HTTPException(status_code=404, detail="no transcribed score yet")
    try:
        return json.loads(storage_client.get_bytes(artifact.object_key))
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404, detail="score artifact missing")


@router.get("/projects/{project_id}/audio")
def get_audio(project_id: str, db: Session = Depends(get_db)) -> FileResponse:
    if db.get(Project, project_id) is None:
        raise HTTPException(status_code=404, detail="project not found")
    artifact = _latest_artifact(db, project_id, "normalize")
    if artifact is None:
        raise HTTPException(status_code=404, detail="no audio artifact yet")
    try:
        path = storage_client.path_for(artifact.object_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="audio artifact missing")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="audio artifact missing")
    return FileResponse(path, media_type="audio/wav", filename="normalized.wav")
