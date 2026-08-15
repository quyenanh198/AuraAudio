from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import MediaAsset, Project


def _seed_project(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.commit()
    return project, asset


def test_repeated_transcription_request_returns_same_job(db_session):
    project, _asset = _seed_project(db_session)
    client = TestClient(create_app())

    with patch("aura_api.routers.jobs.enqueue_transcription_job") as mock_enqueue:
        first = client.post(f"/v1/projects/{project.id}/transcriptions")
        second = client.post(f"/v1/projects/{project.id}/transcriptions")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    mock_enqueue.assert_called_once()


def test_transcription_request_for_unknown_project_is_404():
    client = TestClient(create_app())
    resp = client.post("/v1/projects/does-not-exist/transcriptions")
    assert resp.status_code == 404
