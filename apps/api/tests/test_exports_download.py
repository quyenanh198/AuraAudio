from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import Export, MediaAsset, Project, TranscriptionJob


def _make_succeeded_export(db_session, storage_client, object_key: str, data: bytes) -> str:
    storage_client.put_bytes(object_key, data)
    project = Project(owner_id="anonymous", title="T", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/x/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h", status="succeeded")
    db_session.add(job)
    db_session.flush()
    export = Export(project_id=project.id, job_id=job.id, format="midi", status="succeeded", object_key=object_key)
    db_session.add(export)
    db_session.commit()
    return export.id


def test_export_status_download_url_is_a_local_route(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.exports as exports_module

    monkeypatch.setattr(exports_module, "storage_client", storage.storage_client)

    export_id = _make_succeeded_export(db_session, storage.storage_client, "jobs/1/exports/out.mid", b"MThd-fake")

    client = TestClient(create_app())
    status_resp = client.get(f"/v1/exports/{export_id}")
    assert status_resp.status_code == 200
    download_url = status_resp.json()["download_url"]
    assert download_url == f"/v1/exports/{export_id}/download"

    download_resp = client.get(download_url)
    assert download_resp.status_code == 200
    assert download_resp.content == b"MThd-fake"


def test_export_download_404_not_found(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.exports as exports_module

    monkeypatch.setattr(exports_module, "storage_client", storage.storage_client)

    client = TestClient(create_app())
    resp = client.get("/v1/exports/nonexistent-id/download")
    assert resp.status_code == 404


def test_export_download_404_not_succeeded(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.exports as exports_module

    monkeypatch.setattr(exports_module, "storage_client", storage.storage_client)

    # Create export with status="pending" instead of "succeeded"
    project = Project(owner_id="anonymous", title="T", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/x/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h", status="pending")
    db_session.add(job)
    db_session.flush()
    export = Export(project_id=project.id, job_id=job.id, format="midi", status="pending", object_key="jobs/1/exports/out.mid")
    db_session.add(export)
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/exports/{export.id}/download")
    assert resp.status_code == 404


def test_export_download_404_no_object_key(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.exports as exports_module

    monkeypatch.setattr(exports_module, "storage_client", storage.storage_client)

    # Create export with status="succeeded" but object_key=None
    project = Project(owner_id="anonymous", title="T", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/x/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h", status="succeeded")
    db_session.add(job)
    db_session.flush()
    export = Export(project_id=project.id, job_id=job.id, format="midi", status="succeeded", object_key=None)
    db_session.add(export)
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/exports/{export.id}/download")
    assert resp.status_code == 404


def test_export_download_404_file_missing(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.exports as exports_module

    monkeypatch.setattr(exports_module, "storage_client", storage.storage_client)

    # Create export with valid object_key but don't actually put the file in storage
    project = Project(owner_id="anonymous", title="T", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/x/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h", status="succeeded")
    db_session.add(job)
    db_session.flush()
    export = Export(project_id=project.id, job_id=job.id, format="midi", status="succeeded", object_key="jobs/1/exports/missing.mid")
    db_session.add(export)
    db_session.commit()
    # Note: we DON'T call storage_client.put_bytes(), so the file won't exist on disk

    client = TestClient(create_app())
    resp = client.get(f"/v1/exports/{export.id}/download")
    assert resp.status_code == 404
