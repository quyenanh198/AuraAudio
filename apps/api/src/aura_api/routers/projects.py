from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import MediaAsset, Project
from aura_api.schemas import CreateProjectRequest, ProjectResponse
from aura_api.storage import storage_client

router = APIRouter(tags=["projects"])


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

    project = Project(owner_id="anonymous", title=body.title, instrument=body.instrument)
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
        id=project.id, title=project.title, instrument=project.instrument, media_asset_id=asset.id
    )
