from __future__ import annotations

import boto3

from aura_api.config import settings


class StorageClient:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self.bucket = settings.s3_bucket

    def presign_put(self, key: str, content_type: str, expires_in: int = 900) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )

    def presign_get(self, key: str, expires_in: int = 900) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def head_object(self, key: str) -> dict | None:
        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)
        except self._client.exceptions.ClientError:
            return None


storage_client = StorageClient()
