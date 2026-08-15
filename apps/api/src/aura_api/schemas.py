from __future__ import annotations

import uuid

from pydantic import BaseModel, field_validator

_ALLOWED_CONTENT_TYPES = {
    "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4",
    "video/mp4", "video/quicktime",
}


class CreateUploadRequest(BaseModel):
    filename: str
    content_type: str

    @field_validator("content_type")
    @classmethod
    def content_type_supported(cls, v: str) -> str:
        if v not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"unsupported content_type: {v}")
        return v


class CreateUploadResponse(BaseModel):
    object_key: str
    upload_url: str


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


class ProjectResponse(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    title: str
    instrument: str
    media_asset_id: str
