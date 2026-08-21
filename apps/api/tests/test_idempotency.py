from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import MediaAsset, Project


def _seed_project(db_session, settings=None):
    project = Project(owner_id="anonymous", title="X", instrument="guitar", settings=settings or {})
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


def test_separate_source_toggle_produces_a_distinct_job_not_a_cache_hit(db_session):
    """Detection-quality roadmap item 3's hard constraint: toggling
    Project.settings["separateSource"] must re-transcribe (a new
    TranscriptionJob row, distinct input_hash), not silently return the
    job/cached artifacts from before the flag was set -- see
    aura_api.hashing.compute_input_hash's docstring."""
    project_off, _asset = _seed_project(db_session, settings={"separateSource": False})
    project_on, _asset_on = _seed_project(db_session, settings={"separateSource": True})
    client = TestClient(create_app())

    with patch("aura_api.routers.jobs.enqueue_transcription_job"):
        resp_off = client.post(f"/v1/projects/{project_off.id}/transcriptions")
        resp_on = client.post(f"/v1/projects/{project_on.id}/transcriptions")

    assert resp_off.status_code == 201
    assert resp_on.status_code == 201
    assert resp_off.json()["job_id"] != resp_on.json()["job_id"]


def test_separate_source_toggle_on_same_project_re_transcribes(db_session):
    """Same project, same media -- flipping the setting between two
    transcription requests must not return the earlier job (a 200 cache
    hit), the exact failure mode this hard constraint guards against."""
    project, _asset = _seed_project(db_session, settings={"separateSource": False})
    client = TestClient(create_app())

    with patch("aura_api.routers.jobs.enqueue_transcription_job"):
        first = client.post(f"/v1/projects/{project.id}/transcriptions")
        assert first.status_code == 201

        project.settings = {"separateSource": True}
        db_session.commit()

        second = client.post(f"/v1/projects/{project.id}/transcriptions")

    assert second.status_code == 201
    assert second.json()["job_id"] != first.json()["job_id"]
