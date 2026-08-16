import io

from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_upload_accepts_multipart_file_and_returns_object_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.uploads as uploads_module

    monkeypatch.setattr(uploads_module, "storage_client", storage.storage_client)

    client = TestClient(create_app())
    resp = client.post(
        "/v1/uploads",
        files={"file": ("riff.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")},
    )
    assert resp.status_code == 201
    object_key = resp.json()["object_key"]
    assert object_key.startswith("uploads/")
    assert storage.storage_client.get_bytes(object_key) == b"fake-audio-bytes"


def test_upload_rejects_unsupported_content_type():
    client = TestClient(create_app())
    resp = client.post(
        "/v1/uploads",
        files={"file": ("evil.exe", io.BytesIO(b"x"), "application/x-msdownload")},
    )
    assert resp.status_code == 422


def test_upload_sanitizes_path_traversal_filename(tmp_path, monkeypatch):
    # A malicious multipart filename must never let the client escape the
    # storage root: only the basename should end up in the object key, and
    # the file must land inside the blob root, not outside it.
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.uploads as uploads_module

    monkeypatch.setattr(uploads_module, "storage_client", storage.storage_client)

    client = TestClient(create_app())
    resp = client.post(
        "/v1/uploads",
        files={"file": ("../../evil.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")},
    )
    assert resp.status_code == 201
    object_key = resp.json()["object_key"]
    assert object_key.startswith("uploads/")
    assert ".." not in object_key
    assert object_key.endswith("/evil.wav")

    # The file must have been written under the blob root, not escaped it.
    blob_root = tmp_path / "blobs"
    written_files = list(blob_root.rglob("evil.wav"))
    assert len(written_files) == 1
    assert written_files[0].is_relative_to(blob_root)
    assert not (tmp_path / "evil.wav").exists()


def test_upload_rejects_degenerate_basename(tmp_path, monkeypatch):
    # A degenerate filename like ".." must not write a file at the parent directory
    # itself, breaking the directory structure. Instead, it should fall back to "upload".
    # This test verifies that: (1) uploading ".." succeeds and uses the fallback,
    # (2) a second legitimate upload immediately after still succeeds, proving the
    # uploads/ directory structure wasn't damaged.
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.uploads as uploads_module

    monkeypatch.setattr(uploads_module, "storage_client", storage.storage_client)

    client = TestClient(create_app())

    # First upload with degenerate filename ".."
    resp1 = client.post(
        "/v1/uploads",
        files={"file": ("..", io.BytesIO(b"degenerate-bytes"), "audio/wav")},
    )
    assert resp1.status_code == 201
    object_key1 = resp1.json()["object_key"]
    assert object_key1.startswith("uploads/")
    # Verify it doesn't contain ".." as a path segment (used fallback "upload")
    assert ".." not in object_key1

    # Second upload with normal filename—this is the critical test.
    # If the first upload broke the uploads/ directory, this will fail with 500.
    resp2 = client.post(
        "/v1/uploads",
        files={"file": ("riff.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")},
    )
    assert resp2.status_code == 201
    object_key2 = resp2.json()["object_key"]
    assert object_key2.startswith("uploads/")
    assert object_key2.endswith("/riff.wav")
