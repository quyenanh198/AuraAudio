from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import Export, MediaAsset, Project, TranscriptionJob


def _seed(db, title, status=None, with_exports=False):
    p = Project(owner_id="anonymous", title=title, instrument="guitar")
    db.add(p); db.flush()
    a = MediaAsset(project_id=p.id, kind="source", object_key=f"uploads/x/{title}.wav", duration_ms=31000)
    db.add(a); db.flush()
    if status is not None:
        j = TranscriptionJob(project_id=p.id, media_asset_id=a.id, input_hash=f"h-{title}", status=status, stage="export", progress=100 if status == "succeeded" else 40)
        db.add(j); db.flush()
        if with_exports:
            db.add(Export(project_id=p.id, job_id=j.id, format="midi", status="succeeded", object_key=f"jobs/{j.id}/exports/out.mid"))
            db.add(Export(project_id=p.id, job_id=j.id, format="musicxml", status="succeeded", object_key=f"jobs/{j.id}/exports/out.musicxml"))
    db.commit()
    return p


def test_list_projects_newest_first_with_job_and_exports(db_session):
    _seed(db_session, "older", status="succeeded", with_exports=True)
    _seed(db_session, "newer", status="running")
    client = TestClient(create_app())
    resp = client.get("/v1/projects")
    assert resp.status_code == 200
    items = resp.json()
    assert [i["title"] for i in items] == ["newer", "older"]
    assert items[0]["job"]["status"] == "running" and items[0]["exports"] == []
    assert items[1]["job"]["status"] == "succeeded"
    assert sorted(e["format"] for e in items[1]["exports"]) == ["midi", "musicxml"]
    assert items[1]["duration_ms"] == 31000


def test_list_projects_project_without_job(db_session):
    _seed(db_session, "no-job", status=None)
    client = TestClient(create_app())
    items = client.get("/v1/projects").json()
    assert items[0]["job"] is None and items[0]["exports"] == []


def test_list_projects_exports_survive_succeeded_rederive(db_session):
    # Regression pin for the reported bug: after an edit, the project's
    # LATEST job is a stage="rederive" row (a fresh TranscriptionJob id
    # distinct from the original transcription job the Export rows were
    # created under). The rederive worker updates the project's EXISTING
    # Export rows in place (see rederive.py) rather than re-pointing them at
    # the rederive job's id, so `list_projects` must resolve exports by
    # project_id, not by the latest job's id.
    p = _seed(db_session, "edited", status="succeeded", with_exports=True)
    rederive_job = TranscriptionJob(
        project_id=p.id, media_asset_id=p.media_assets[0].id,
        input_hash="rederive-1", status="succeeded", stage="rederive", progress=100,
    )
    db_session.add(rederive_job)
    db_session.commit()

    client = TestClient(create_app())
    items = client.get("/v1/projects").json()
    assert len(items) == 1
    item = items[0]
    assert item["job"]["stage"] == "rederive" and item["job"]["status"] == "succeeded"
    assert sorted(e["format"] for e in item["exports"]) == ["midi", "musicxml"]


def test_list_projects_exports_survive_running_rederive(db_session):
    # Same bug, but the latest rederive job is still RUNNING (not yet
    # succeeded) — exports created by the earlier, completed transcription
    # must still be listed; only the `job` field reflects the in-progress
    # rederive.
    p = _seed(db_session, "editing", status="succeeded", with_exports=True)
    rederive_job = TranscriptionJob(
        project_id=p.id, media_asset_id=p.media_assets[0].id,
        input_hash="rederive-2", status="running", stage="rederive", progress=40,
    )
    db_session.add(rederive_job)
    db_session.commit()

    client = TestClient(create_app())
    items = client.get("/v1/projects").json()
    assert len(items) == 1
    item = items[0]
    assert item["job"]["stage"] == "rederive" and item["job"]["status"] == "running"
    assert sorted(e["format"] for e in item["exports"]) == ["midi", "musicxml"]
