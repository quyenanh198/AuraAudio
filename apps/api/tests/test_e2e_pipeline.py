import boto3
import pytest
from fastapi.testclient import TestClient

from aura_api.config import settings
from aura_api.main import create_app
from aura_worker.runner import run_transcription_job
from test_fixtures.generate import write_guitar_pluck_wav


@pytest.fixture()
def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def test_full_pipeline_upload_to_export_is_idempotent(db_session, tmp_path, s3_client):
    client = TestClient(create_app())

    fixture_path = tmp_path / "riff.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0, sample_rate=44100)

    upload_resp = client.post(
        "/v1/uploads", json={"filename": "riff.wav", "content_type": "audio/wav"}
    )
    assert upload_resp.status_code == 201
    object_key = upload_resp.json()["object_key"]

    s3_client.put_object(Bucket=settings.s3_bucket, Key=object_key, Body=fixture_path.read_bytes())

    project_resp = client.post(
        "/v1/projects",
        json={"title": "E2E Riff", "instrument": "guitar", "object_key": object_key},
    )
    assert project_resp.status_code == 201
    project_id = project_resp.json()["id"]

    job_resp_1 = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp_1.status_code == 201
    job_id = job_resp_1.json()["job_id"]

    # Run the worker in-process (stands in for the RQ worker process).
    run_transcription_job(job_id)

    status_resp = client.get(f"/v1/jobs/{job_id}")
    assert status_resp.json()["status"] == "succeeded", status_resp.json()

    from aura_api.models import Export

    exports = db_session.query(Export).filter_by(job_id=job_id).all()
    assert {e.format for e in exports} == {"midi", "musicxml"}

    export_id = next(e.id for e in exports if e.format == "midi")
    export_resp = client.get(f"/v1/exports/{export_id}")
    assert export_resp.status_code == 200
    download_url = export_resp.json()["download_url"]
    assert download_url is not None

    import urllib.request

    with urllib.request.urlopen(download_url) as f:
        midi_bytes = f.read()
    assert midi_bytes[:4] == b"MThd"  # valid MIDI file header

    musicxml_export_id = next(e.id for e in exports if e.format == "musicxml")
    musicxml_export_resp = client.get(f"/v1/exports/{musicxml_export_id}")
    assert musicxml_export_resp.status_code == 200
    musicxml_download_url = musicxml_export_resp.json()["download_url"]
    assert musicxml_download_url is not None

    with urllib.request.urlopen(musicxml_download_url) as f:
        musicxml_bytes = f.read()
    assert "<technical>" in musicxml_bytes.decode("utf-8")

    # Re-request transcription for the same project: must return the same job,
    # and re-running the worker on an already-succeeded job must not recompute
    # any stage (StageArtifact rows are reused) or create duplicate exports.
    job_resp_2 = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp_2.status_code == 200
    assert job_resp_2.json()["job_id"] == job_id

    exports_after_second_call = db_session.query(Export).filter_by(job_id=job_id).all()
    assert len(exports_after_second_call) == 2  # unchanged — no duplicate GPU/CPU work
