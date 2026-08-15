from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import Export
from aura_api.schemas import ExportStatusResponse
from aura_api.storage import storage_client

router = APIRouter(tags=["exports"])


@router.get("/exports/{export_id}", response_model=ExportStatusResponse)
def get_export(export_id: str, db: Session = Depends(get_db)) -> ExportStatusResponse:
    export = db.get(Export, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")

    download_url = None
    if export.status == "succeeded" and export.object_key:
        download_url = storage_client.presign_get(export.object_key)

    return ExportStatusResponse(
        id=export.id, format=export.format, status=export.status, download_url=download_url
    )
