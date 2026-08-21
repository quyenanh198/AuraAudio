from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import MediaAsset, Project


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


def test_create_project_stores_separate_source_setting(db_session, tmp_path, monkeypatch):
    """Detection-quality roadmap item 3: the opt-in "isolate instrument
    from mix" toggle is a Project setting (Project.settings JSON column,
    no DB migration), set at project-creation time."""
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.projects as projects_module

    monkeypatch.setattr(projects_module, "storage_client", storage.storage_client)

    object_key = "uploads/abc/mix.wav"
    storage.storage_client.put_bytes(object_key, b"fake-audio-bytes")

    client = TestClient(create_app())
    resp = client.post(
        "/v1/projects",
        json={
            "title": "Mixed Recording",
            "instrument": "guitar",
            "object_key": object_key,
            "separate_source": True,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["separate_source"] is True

    project = db_session.query(Project).filter_by(id=body["id"]).first()
    assert project.settings.get("separateSource") is True


def test_create_project_defaults_separate_source_to_false(db_session, tmp_path, monkeypatch):
    """Hard constraint: never default to opt-in -- omitting the field
    entirely (matching every pre-existing caller/test) must stay off."""
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.projects as projects_module

    monkeypatch.setattr(projects_module, "storage_client", storage.storage_client)

    object_key = "uploads/abc/solo.wav"
    storage.storage_client.put_bytes(object_key, b"fake-audio-bytes")

    client = TestClient(create_app())
    resp = client.post(
        "/v1/projects",
        json={"title": "Solo", "instrument": "guitar", "object_key": object_key},
    )
    assert resp.status_code == 201
    assert resp.json()["separate_source"] is False


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


def test_create_project_rejects_path_traversal_object_key_with_404(
    db_session, tmp_path, monkeypatch
):
    # A client-supplied object_key that attempts to escape the storage root
    # (e.g. an absolute path, or "../.." segments) must be treated like a
    # missing object (404), never surfaced as an unhandled 500.
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.projects as projects_module

    monkeypatch.setattr(projects_module, "storage_client", storage.storage_client)

    client = TestClient(create_app())

    for escaping_key in ["/etc/passwd", "../../../etc/passwd"]:
        resp = client.post(
            "/v1/projects",
            json={"title": "X", "instrument": "piano", "object_key": escaping_key},
        )
        assert resp.status_code == 404, (escaping_key, resp.status_code, resp.text)
