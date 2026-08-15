from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_create_project_registers_media_asset(db_session):
    client = TestClient(create_app())
    with patch("aura_api.routers.projects.storage_client") as mock_storage:
        mock_storage.head_object.return_value = {"ContentLength": 1024}
        resp = client.post(
            "/v1/projects",
            json={
                "title": "My Riff",
                "instrument": "guitar",
                "object_key": "uploads/abc/riff.wav",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "My Riff"
    assert body["instrument"] == "guitar"
    assert "id" in body
    assert "media_asset_id" in body


def test_create_project_rejects_missing_object():
    client = TestClient(create_app())
    with patch("aura_api.routers.projects.storage_client") as mock_storage:
        mock_storage.head_object.return_value = None
        resp = client.post(
            "/v1/projects",
            json={"title": "X", "instrument": "piano", "object_key": "uploads/missing.wav"},
        )
    assert resp.status_code == 404
