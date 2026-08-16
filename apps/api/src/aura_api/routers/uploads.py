from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from aura_api.schemas import _ALLOWED_CONTENT_TYPES, CreateUploadResponse, make_upload_object_key
from aura_api.storage import storage_client

router = APIRouter(tags=["uploads"])


@router.post("/uploads", response_model=CreateUploadResponse, status_code=201)
async def create_upload(file: UploadFile = File(...)) -> CreateUploadResponse:
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=f"unsupported content_type: {file.content_type}")
    name = Path(file.filename or "").name
    object_key = make_upload_object_key(name if name not in ("", ".", "..") else "upload")
    data = await file.read()
    storage_client.put_bytes(object_key, data)
    return CreateUploadResponse(object_key=object_key)
