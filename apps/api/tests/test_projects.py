from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import MediaAsset


def test_create_project_registers_media_asset(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.projects as projects_module

    monkeypatch.setattr(projects_module, "storage_client", storage.storage_client)

    # Put a file in storage with known size
    audio_data = b"fake-audio-bytes" * 64  # 1024 bytes
    object_key = "uploads/abc/riff.wav"
    storage.storage_client.put_bytes(object_key, audio_data)

    client = TestClient(create_app())
    resp = client.post(
        "/v1/projects",
        json={
            "title": "My Riff",
            "instrument": "guitar",
            "object_key": object_key,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "My Riff"
    assert body["instrument"] == "guitar"
    assert "id" in body
    media_asset_id = body["media_asset_id"]
    assert media_asset_id is not None

    # Verify that MediaAsset.bytes was set correctly from head_object's ContentLength
    media_asset = db_session.query(MediaAsset).filter_by(id=media_asset_id).first()
    assert media_asset is not None
    assert media_asset.bytes == 1024
    assert media_asset.object_key == object_key


def test_create_project_rejects_missing_object(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.projects as projects_module

    monkeypatch.setattr(projects_module, "storage_client", storage.storage_client)

    client = TestClient(create_app())
    resp = client.post(
        "/v1/projects",
        json={"title": "X", "instrument": "piano", "object_key": "uploads/missing.wav"},
    )
    assert resp.status_code == 404
