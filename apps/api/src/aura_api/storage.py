from __future__ import annotations

import shutil
from pathlib import Path

from aura_api.config import settings


class LocalStorageClient:
    def __init__(self) -> None:
        self.root = Path(settings.data_dir) / "blobs"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key: str) -> Path:
        root = self.root.resolve()
        path = (root / key).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"key escapes storage root: {key!r}")
        return path

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self.path_for(key).read_bytes()

    def download_media_asset(self, key: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path_for(key), dest_path)
        return dest_path

    def head_object(self, key: str) -> dict | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        return {"ContentLength": path.stat().st_size}


storage_client = LocalStorageClient()
