from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import Export, MediaAsset, Project, TranscriptionJob


def test_get_job_status(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(
        project_id=project.id, media_asset_id=asset.id, input_hash="h1",
        status="running", stage="inference", progress=40,
    )
    db_session.add(job)
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["stage"] == "inference"
    assert body["progress"] == 40


def test_get_job_status_404_for_unknown_job():
    client = TestClient(create_app())
    resp = client.get("/v1/jobs/does-not-exist")
    assert resp.status_code == 404


def test_get_export_returns_download_url_when_succeeded(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h2", status="succeeded")
    db_session.add(job)
    db_session.flush()
    export = Export(
        project_id=project.id, job_id=job.id, format="midi", status="succeeded",
        object_key="exports/a/out.mid",
    )
    db_session.add(export)
    db_session.commit()

    client = TestClient(create_app())
    with patch("aura_api.routers.exports.storage_client") as mock_storage:
        mock_storage.presign_get.return_value = "https://minio.local/signed-download"
        resp = client.get(f"/v1/exports/{export.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["download_url"] == "https://minio.local/signed-download"
