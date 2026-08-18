import json
import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from aura_api.models import MediaAsset, Project, ScoreRevision, StageArtifact, TranscriptionJob


def _score(events=None, meter="4/4", tempo=120.0):
    """Mirrors packages/score_schema/tests/test_edits.py's `_score()` shape —
    a minimal, valid schema-v4 score with a string/fret set on its one note,
    so guitar-specific ops (set_fingering) and the assign-artifact round
    trip both have real data to exercise."""
    spb = 60.0 / tempo
    default_events = [{
        "id": "note_00", "pitch": 52, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None,
    }]
    return {
        "schemaVersion": 4,
        "timeMap": [{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": spb}],
        "parts": [{
            "instrument": "guitar", "tempoBpm": tempo, "meter": meter, "key": "E minor",
            "confidence": {"tempo": 0.9, "meter": 0.8, "key": 0.7},
            "measures": [{"number": 1, "events": events or default_events}],
        }],
    }


def _patch_storage_and_queue(monkeypatch, tmp_path):
    """Follows test_scores_endpoints.py's storage-monkeypatch idiom, plus
    patches aura_api.routers.edits.enqueue_rederive_job to a no-op recorder
    so the rederive queue never actually runs in tests."""
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage
    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())

    import aura_api.routers.scores as scores_module
    monkeypatch.setattr(scores_module, "storage_client", storage.storage_client)

    import aura_api.routers.edits as edits_module
    monkeypatch.setattr(edits_module, "storage_client", storage.storage_client)

    recorded = []
    monkeypatch.setattr(edits_module, "enqueue_rederive_job", lambda job_id: recorded.append(job_id))

    return storage.storage_client, recorded


def _project_with_assign_artifact(db, storage, score=None):
    """Mirrors test_scores_endpoints.py's `_project_with_job` helper, plus
    seeds a succeeded job's `assign` StageArtifact whose blob is a real
    minimal schema-v4 score."""
    p = Project(owner_id="anonymous", title="T", instrument="guitar")
    db.add(p); db.flush()
    a = MediaAsset(project_id=p.id, kind="source", object_key="uploads/x/r.wav")
    db.add(a); db.flush()
    j = TranscriptionJob(project_id=p.id, media_asset_id=a.id, input_hash="h", status="succeeded")
    db.add(j); db.flush()

    payload = score or _score()
    key = f"jobs/{j.id}/stage/assign.json"
    storage.put_bytes(key, json.dumps(payload).encode())
    db.add(StageArtifact(job_id=j.id, stage="assign", version=1, object_key=key, sha256="x"))
    db.commit()
    return p, j, payload


def test_first_edit_bootstraps_baseline_and_creates_revision_with_rederive_job(db_session, tmp_path, monkeypatch):
    storage, recorded = _patch_storage_and_queue(monkeypatch, tmp_path)
    p, j, original = _project_with_assign_artifact(db_session, storage)

    client = TestClient(_app())
    resp = client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["score"]["parts"][0]["measures"][0]["events"][0]["pitch"] == 60
    assert body["rederive_job_id"] in recorded  # queue never actually ran, but was asked to

    revisions = db_session.query(ScoreRevision).filter(ScoreRevision.project_id == p.id).all()
    assert len(revisions) == 2  # baseline (rev 0) + the applied edit (rev 1)
    baseline = next(r for r in revisions if r.revision == 0)
    assert baseline.created_by == "baseline"
    assert baseline.score_json == original

    db_session.refresh(p)
    assert p.settings["scoreHeadRevisionId"] is not None

    job_resp = client.get(f"/v1/jobs/{body['rederive_job_id']}")
    assert job_resp.status_code == 200
    assert job_resp.json()["stage"] == "rederive"


def test_get_score_reflects_head_across_undo_and_redo(db_session, tmp_path, monkeypatch):
    storage, _ = _patch_storage_and_queue(monkeypatch, tmp_path)
    p, j, original = _project_with_assign_artifact(db_session, storage)
    client = TestClient(_app())

    edit_resp = client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    edited_score = edit_resp.json()["score"]

    after_edit = client.get(f"/v1/projects/{p.id}/score").json()
    assert after_edit == edited_score

    undo_resp = client.post(f"/v1/projects/{p.id}/edits/undo")
    assert undo_resp.status_code == 200
    assert undo_resp.json()["score"] == original
    after_undo = client.get(f"/v1/projects/{p.id}/score").json()
    assert after_undo == original

    redo_resp = client.post(f"/v1/projects/{p.id}/edits/redo")
    assert redo_resp.status_code == 200
    assert redo_resp.json()["score"] == edited_score
    after_redo = client.get(f"/v1/projects/{p.id}/score").json()
    assert after_redo == edited_score


def test_undo_at_baseline_and_redo_at_newest_return_409(db_session, tmp_path, monkeypatch):
    storage, _ = _patch_storage_and_queue(monkeypatch, tmp_path)
    p, j, original = _project_with_assign_artifact(db_session, storage)
    client = TestClient(_app())

    client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 60})

    # newest: redo should 409
    assert client.post(f"/v1/projects/{p.id}/edits/redo").status_code == 409

    # rewind to baseline: undo should now 409
    assert client.post(f"/v1/projects/{p.id}/edits/undo").status_code == 200
    assert client.post(f"/v1/projects/{p.id}/edits/undo").status_code == 409


def test_edit_while_rewound_truncates_future_revisions(db_session, tmp_path, monkeypatch):
    storage, _ = _patch_storage_and_queue(monkeypatch, tmp_path)
    p, j, original = _project_with_assign_artifact(db_session, storage)
    client = TestClient(_app())

    # edit A
    client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    # edit B
    client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 61})
    # undo back to A
    assert client.post(f"/v1/projects/{p.id}/edits/undo").status_code == 200
    # edit C from A, discarding B
    edit_c = client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 62})
    assert edit_c.status_code == 200
    c_score = edit_c.json()["score"]

    # B's revision (and anything above the old head) must be gone
    revisions = db_session.query(ScoreRevision).filter(ScoreRevision.project_id == p.id).all()
    pitches = {r.revision: r.score_json["parts"][0]["measures"][0]["events"][0]["pitch"] for r in revisions}
    assert 61 not in pitches.values()

    assert client.post(f"/v1/projects/{p.id}/edits/redo").status_code == 409

    head_score = client.get(f"/v1/projects/{p.id}/score").json()
    assert head_score == c_score


def test_invalid_op_returns_422_with_reason_and_creates_no_revision(db_session, tmp_path, monkeypatch):
    storage, _ = _patch_storage_and_queue(monkeypatch, tmp_path)
    p, j, original = _project_with_assign_artifact(db_session, storage)
    client = TestClient(_app())

    # establish a head first so the invalid attempt below can't be confused
    # with baseline bootstrapping
    client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    before = db_session.query(ScoreRevision).filter(ScoreRevision.project_id == p.id).count()

    resp = client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 128})
    assert resp.status_code == 422
    assert "pitch" in resp.json()["detail"]

    after = db_session.query(ScoreRevision).filter(ScoreRevision.project_id == p.id).count()
    assert after == before


def _separate_session():
    """A second Session/connection, distinct from the `db_session` fixture
    the request handler itself used, bound to the same test database.
    Verifying through it (rather than through `db_session`, whose identity
    map could paper over a bug) is the guard against the invalid-FIRST-edit
    path leaving a flushed-but-uncommitted row that only *looks* absent
    because of same-session object caching."""
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
    return sessionmaker(bind=engine)()


def test_invalid_first_edit_leaves_no_trace_and_valid_first_edit_still_bootstraps(db_session, tmp_path, monkeypatch):
    storage, recorded = _patch_storage_and_queue(monkeypatch, tmp_path)
    p, j, original = _project_with_assign_artifact(db_session, storage)
    client = TestClient(_app())

    # The very first edit on this project, and it's invalid. Before the fix,
    # this branch flushed a baseline ScoreRevision row (uncommitted) before
    # apply_edit ran, and relied on get_db's close-time implicit rollback to
    # discard it. This must now be false by construction, not by accident of
    # session teardown timing.
    resp = client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 128})
    assert resp.status_code == 422
    assert "pitch" in resp.json()["detail"]
    assert recorded == []  # enqueue_rederive_job was never called

    check = _separate_session()
    try:
        assert check.query(ScoreRevision).filter(ScoreRevision.project_id == p.id).count() == 0
        fresh_project = check.get(Project, p.id)
        assert "scoreHeadRevisionId" not in (fresh_project.settings or {})
        rederive_jobs = (
            check.query(TranscriptionJob)
            .filter(TranscriptionJob.project_id == p.id, TranscriptionJob.stage == "rederive")
            .all()
        )
        assert rederive_jobs == []
    finally:
        check.close()

    # A subsequent VALID first edit must still bootstrap correctly — the
    # restructure must not have broken the happy path.
    valid_resp = client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    assert valid_resp.status_code == 200
    body = valid_resp.json()
    assert body["version"] == 1
    assert body["score"]["parts"][0]["measures"][0]["events"][0]["pitch"] == 60

    revisions = db_session.query(ScoreRevision).filter(ScoreRevision.project_id == p.id).all()
    assert len(revisions) == 2  # baseline (rev 0) + the applied edit (rev 1)
    baseline = next(r for r in revisions if r.revision == 0)
    assert baseline.created_by == "baseline"
    assert baseline.score_json == original


def test_revert_returns_head_to_baseline(db_session, tmp_path, monkeypatch):
    storage, _ = _patch_storage_and_queue(monkeypatch, tmp_path)
    p, j, original = _project_with_assign_artifact(db_session, storage)
    client = TestClient(_app())

    client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    client.post(f"/v1/projects/{p.id}/edits", json={"type": "set_pitch", "eventId": "note_00", "pitch": 61})

    resp = client.post(f"/v1/projects/{p.id}/edits/revert")
    assert resp.status_code == 200
    assert resp.json()["score"] == original

    db_session.refresh(p)
    head = db_session.get(ScoreRevision, p.settings["scoreHeadRevisionId"])
    assert head.created_by == "baseline"
    assert head.score_json == original


def _app():
    from aura_api.main import create_app
    return create_app()
