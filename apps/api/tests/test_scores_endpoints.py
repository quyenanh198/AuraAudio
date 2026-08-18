import json
from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import MediaAsset, Project, StageArtifact, TranscriptionJob


def _project_with_job(db, status="succeeded"):
    p = Project(owner_id="anonymous", title="T", instrument="guitar")
    db.add(p); db.flush()
    a = MediaAsset(project_id=p.id, kind="source", object_key="uploads/x/r.wav")
    db.add(a); db.flush()
    j = TranscriptionJob(project_id=p.id, media_asset_id=a.id, input_hash="h", status=status)
    db.add(j); db.flush()
    db.commit()
    return p, j


def test_score_endpoint_returns_assign_artifact(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage
    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.scores as scores_module
    monkeypatch.setattr(scores_module, "storage_client", storage.storage_client)

    p, j = _project_with_job(db_session)
    payload = {"schemaVersion": 4, "parts": []}
    storage.storage_client.put_bytes(f"jobs/{j.id}/stage/assign.json", json.dumps(payload).encode())
    db_session.add(StageArtifact(job_id=j.id, stage="assign", version=2, object_key=f"jobs/{j.id}/stage/assign.json", sha256="x"))
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/projects/{p.id}/score")
    assert resp.status_code == 200 and resp.json() == payload


def test_score_endpoint_404_without_succeeded_job(db_session):
    p, _ = _project_with_job(db_session, status="running")
    client = TestClient(create_app())
    assert client.get(f"/v1/projects/{p.id}/score").status_code == 404


def test_audio_endpoint_serves_normalize_artifact(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage
    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.scores as scores_module
    monkeypatch.setattr(scores_module, "storage_client", storage.storage_client)

    p, j = _project_with_job(db_session)
    storage.storage_client.put_bytes(f"jobs/{j.id}/stage/normalized.wav", b"RIFF-fake")
    db_session.add(StageArtifact(job_id=j.id, stage="normalize", version=1, object_key=f"jobs/{j.id}/stage/normalized.wav", sha256="y"))
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/projects/{p.id}/audio")
    assert resp.status_code == 200 and resp.content == b"RIFF-fake"
