from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_create_upload_returns_signed_url_and_object_key():
    client = TestClient(create_app())
    with patch("aura_api.routers.uploads.storage_client") as mock_storage:
        mock_storage.presign_put.return_value = "https://minio.local/signed"
        resp = client.post(
            "/v1/uploads", json={"filename": "riff.wav", "content_type": "audio/wav"}
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["upload_url"] == "https://minio.local/signed"
    assert body["object_key"].startswith("uploads/")
    assert body["object_key"].endswith("riff.wav")


def test_create_upload_rejects_unsupported_content_type():
    client = TestClient(create_app())
    resp = client.post(
        "/v1/uploads", json={"filename": "riff.exe", "content_type": "application/octet-stream"}
    )
    assert resp.status_code == 422
