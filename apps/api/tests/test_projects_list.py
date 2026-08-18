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
