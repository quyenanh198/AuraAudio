from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import Export
from aura_api.schemas import ExportStatusResponse
from aura_api.storage import storage_client

router = APIRouter(tags=["exports"])

_MEDIA_TYPES = {"midi": "audio/midi", "musicxml": "application/xml"}


@router.get("/exports/{export_id}", response_model=ExportStatusResponse)
def get_export(export_id: str, db: Session = Depends(get_db)) -> ExportStatusResponse:
    export = db.get(Export, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")

    download_url = None
    if export.status == "succeeded" and export.object_key:
        download_url = f"/v1/exports/{export.id}/download"

    return ExportStatusResponse(
        id=export.id, format=export.format, status=export.status, download_url=download_url
    )


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, db: Session = Depends(get_db)) -> FileResponse:
    export = db.get(Export, export_id)
    if export is None or export.status != "succeeded" or not export.object_key:
        raise HTTPException(status_code=404, detail="export not available")

    try:
        path = storage_client.path_for(export.object_key)
    except ValueError:
        # object_key resolved outside the storage root — defensive, since
        # this key comes from the DB rather than directly from the client.
        raise HTTPException(status_code=404, detail="export file missing") from None
    if not path.is_file():
        raise HTTPException(status_code=404, detail="export file missing")

    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(export.format, "application/octet-stream"),
        filename=path.name,
    )
