from __future__ import annotations

import os
from pathlib import Path

import boto3


class WorkerStorageClient:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            region_name=os.environ.get("S3_REGION", "us-east-1"),
        )
        self.bucket = os.environ["S3_BUCKET"]

    def download_media_asset(self, object_key: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, object_key, str(dest_path))
        return dest_path

    def put_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def put_file(self, key: str, local_path: Path) -> None:
        self._client.upload_file(str(local_path), self.bucket, key)

    def get_file(self, key: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(dest_path))
        return dest_path
