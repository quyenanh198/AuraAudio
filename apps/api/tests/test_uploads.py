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
