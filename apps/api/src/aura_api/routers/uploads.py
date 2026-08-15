from fastapi import APIRouter

from aura_api.schemas import CreateUploadRequest, CreateUploadResponse, make_upload_object_key
from aura_api.storage import storage_client

router = APIRouter(tags=["uploads"])


@router.post("/uploads", response_model=CreateUploadResponse, status_code=201)
def create_upload(body: CreateUploadRequest) -> CreateUploadResponse:
    object_key = make_upload_object_key(body.filename)
    upload_url = storage_client.presign_put(object_key, body.content_type)
    return CreateUploadResponse(object_key=object_key, upload_url=upload_url)
