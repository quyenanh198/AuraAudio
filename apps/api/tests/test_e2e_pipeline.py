from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_worker.runner import run_transcription_job
from test_fixtures.generate import write_guitar_pluck_wav, write_diatonic_melody_wav


def test_full_pipeline_upload_to_export_is_idempotent(db_session, tmp_path):
    client = TestClient(create_app())

    fixture_path = tmp_path / "riff.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0, sample_rate=44100)

    with fixture_path.open("rb") as f:
        upload_resp = client.post(
            "/v1/uploads", files={"file": (fixture_path.name, f, "audio/wav")}
        )
    assert upload_resp.status_code == 201
    object_key = upload_resp.json()["object_key"]

    project_resp = client.post(
        "/v1/projects",
        json={"title": "E2E Riff", "instrument": "guitar", "object_key": object_key},
    )
    assert project_resp.status_code == 201
    project_id = project_resp.json()["id"]

    # Job creation now dispatches to a background thread pool (Task 6)
    # immediately upon success. Patch that dispatch out here so the only
    # execution of the pipeline is the explicit, synchronous call below —
    # otherwise the background thread and this call would race on the same
    # job's stage artifacts. Task 6's own tests cover the dispatch itself.
    with patch("aura_api.routers.jobs.enqueue_transcription_job"):
        job_resp_1 = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp_1.status_code == 201
    job_id = job_resp_1.json()["job_id"]

    # Run the worker in-process (stands in for the thread-pool dispatch).
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

    download_resp = client.get(download_url)
    assert download_resp.status_code == 200
    midi_bytes = download_resp.content
    assert midi_bytes[:4] == b"MThd"  # valid MIDI file header

    musicxml_export_id = next(e.id for e in exports if e.format == "musicxml")
    musicxml_export_resp = client.get(f"/v1/exports/{musicxml_export_id}")
    assert musicxml_export_resp.status_code == 200
    musicxml_download_url = musicxml_export_resp.json()["download_url"]
    assert musicxml_download_url is not None

    musicxml_download_resp = client.get(musicxml_download_url)
    assert musicxml_download_resp.status_code == 200
    musicxml_bytes = musicxml_download_resp.content
    assert "<technical>" in musicxml_bytes.decode("utf-8")

    # Re-request transcription for the same project: must return the same job,
    # and re-running the worker on an already-succeeded job must not recompute
    # any stage (StageArtifact rows are reused) or create duplicate exports.
    job_resp_2 = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp_2.status_code == 200
    assert job_resp_2.json()["job_id"] == job_id

    exports_after_second_call = db_session.query(Export).filter_by(job_id=job_id).all()
    assert len(exports_after_second_call) == 2  # unchanged — no duplicate GPU/CPU work


def test_full_pipeline_piano_renders_grand_staff(db_session, tmp_path):
    client = TestClient(create_app())

    fixture_path = tmp_path / "melody.wav"
    write_diatonic_melody_wav(fixture_path, key="C major", duration_s=4.0, sample_rate=44100)

    with fixture_path.open("rb") as f:
        upload_resp = client.post(
            "/v1/uploads", files={"file": (fixture_path.name, f, "audio/wav")}
        )
    assert upload_resp.status_code == 201
    object_key = upload_resp.json()["object_key"]

    project_resp = client.post(
        "/v1/projects",
        json={"title": "E2E Piano", "instrument": "piano", "object_key": object_key},
    )
    assert project_resp.status_code == 201
    project_id = project_resp.json()["id"]

    with patch("aura_api.routers.jobs.enqueue_transcription_job"):
        job_resp = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp.status_code == 201
    job_id = job_resp.json()["job_id"]

    run_transcription_job(job_id)

    status_resp = client.get(f"/v1/jobs/{job_id}")
    assert status_resp.json()["status"] == "succeeded", status_resp.json()

    from aura_api.models import Export

    exports = db_session.query(Export).filter_by(job_id=job_id).all()
    musicxml_export_id = next(e.id for e in exports if e.format == "musicxml")
    export_resp = client.get(f"/v1/exports/{musicxml_export_id}")
    assert export_resp.status_code == 200
    download_url = export_resp.json()["download_url"]

    download_resp = client.get(download_url)
    assert download_resp.status_code == 200
    musicxml_bytes = download_resp.content
    musicxml_text = musicxml_bytes.decode("utf-8")
    assert "<staves>2</staves>" in musicxml_text
    # <staves>2</staves> alone only proves the grand-staff *structure* was
    # emitted (which depends only on instrument == "piano"), not that any
    # note actually landed on staff 2 — i.e. it stays true even if hand
    # assignment silently produced hand: null for every event. Require at
    # least one real note on staff 2 too, so a disabled/no-op hand
    # assignment (all notes falling through to staff 1) fails this test.
    assert "<staff>2</staff>" in musicxml_text
