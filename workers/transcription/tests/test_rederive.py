from datetime import datetime, timedelta

from aura_api.models import Export, MediaAsset, Project, ScoreRevision, TranscriptionJob
from aura_api.storage import LocalStorageClient

from aura_worker.rederive import run_rederive_job


def _score(locked_string: int = 2, locked_fret: int = 2, tempo_bpm: float = 120.0) -> dict:
    """One measure: event 0 is LOCKED to a real-but-non-default guitar
    placement (string 2, not the open-string-0 default candidate_for_pitch
    would otherwise favor); event 1 is unlocked and must receive a fresh DP
    assignment on rederive."""
    return {
        "schemaVersion": 4,
        "timeMap": [{"beat": 0, "seconds": 0.0}],
        "parts": [{
            "instrument": "guitar",
            "tempoBpm": tempo_bpm,
            "meter": "4/4",
            "key": "C major",
            "confidence": {"tempo": 0.9, "meter": 0.8, "key": 0.7},
            "measures": [{
                "number": 1,
                "events": [
                    {
                        "id": "note_00", "pitch": 52, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.9, "locked": True,
                        "string": locked_string, "fret": locked_fret, "hand": None,
                    },
                    {
                        "id": "note_01", "pitch": 57, "onsetSeconds": 0.5, "offsetSeconds": 1.0,
                        "notatedOnset": "1/4", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.9, "locked": False,
                        "string": None, "fret": None, "hand": None,
                    },
                ],
            }],
        }],
    }


def _score_with_silent_measure(locked_string: int = 2, locked_fret: int = 2) -> dict:
    """Same as _score, but with a trailing silent measure (2) carrying no
    events — the shape quantize.py's silent-measure fidelity fix now
    produces for a mid-clip gap — to make sure rederive's full pipeline
    (DP reassignment, MIDI write, MusicXML export) tolerates it."""
    score = _score(locked_string, locked_fret)
    score["parts"][0]["measures"].append({"number": 2, "events": []})
    return score


def _arrange_project(db_session, *, locked_string: int = 2, locked_fret: int = 2, score_json: dict | None = None):
    """Bootstraps a guitar project with a succeeded transcription job, a head
    ScoreRevision (with one locked + one unlocked event), and the transcription's
    original midi/musicxml Export rows — the state Task 4's rederive job
    creation contract assumes already exists before a rederive job runs."""
    project = Project(owner_id="anonymous", title="Riff", instrument="guitar")
    db_session.add(project)
    db_session.flush()

    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.flush()

    orig_job = TranscriptionJob(
        project_id=project.id, media_asset_id=asset.id, input_hash="orig-hash",
        status="succeeded", stage="export", progress=100,
    )
    db_session.add(orig_job)
    db_session.flush()

    revision = ScoreRevision(
        project_id=project.id, revision=0,
        score_json=score_json if score_json is not None else _score(locked_string, locked_fret),
    )
    db_session.add(revision)
    db_session.flush()

    project.settings = {"scoreHeadRevisionId": revision.id}

    original_midi_key = f"jobs/{orig_job.id}/exports/output.mid"
    original_xml_key = f"jobs/{orig_job.id}/exports/output.musicxml"
    midi_export = Export(
        project_id=project.id, job_id=orig_job.id, revision=0, format="midi",
        status="succeeded", object_key=original_midi_key,
    )
    xml_export = Export(
        project_id=project.id, job_id=orig_job.id, revision=0, format="musicxml",
        status="succeeded", object_key=original_xml_key,
    )
    db_session.add_all([midi_export, xml_export])
    db_session.commit()

    return {
        "project": project,
        "asset": asset,
        "orig_job": orig_job,
        "revision": revision,
        "midi_export": midi_export,
        "xml_export": xml_export,
        "original_midi_key": original_midi_key,
        "original_xml_key": original_xml_key,
    }


def _make_rederive_job(db_session, project, asset, *, input_hash: str, created_at=None):
    job = TranscriptionJob(
        project_id=project.id, media_asset_id=asset.id, input_hash=input_hash,
        status="queued", stage="rederive", progress=0,
    )
    if created_at is not None:
        job.created_at = created_at
    db_session.add(job)
    db_session.commit()
    return job


def test_rederive_reassigns_unlocked_updates_revision_and_exports(db_session):
    # Arrange
    ctx = _arrange_project(db_session)
    rederive_job = _make_rederive_job(db_session, ctx["project"], ctx["asset"], input_hash="rederive-1")

    # Act
    run_rederive_job(rederive_job.id)
    db_session.expire_all()  # pick up the writes run_rederive_job committed on its own session

    # Assert
    refreshed_job = db_session.get(TranscriptionJob, rederive_job.id)
    assert refreshed_job.status == "succeeded"
    assert refreshed_job.progress == 100

    refreshed_revision = db_session.get(ScoreRevision, ctx["revision"].id)
    events = refreshed_revision.score_json["parts"][0]["measures"][0]["events"]
    locked_event = next(e for e in events if e["id"] == "note_00")
    unlocked_event = next(e for e in events if e["id"] == "note_01")
    assert (locked_event["string"], locked_event["fret"]) == (2, 2)  # kept its locked placement
    assert unlocked_event["string"] is not None  # got a fresh DP assignment
    assert unlocked_event["fret"] is not None

    project_id = ctx["project"].id
    expected_base = f"projects/{project_id}/exports/rev0"
    refreshed_midi = db_session.get(Export, ctx["midi_export"].id)
    refreshed_xml = db_session.get(Export, ctx["xml_export"].id)
    assert refreshed_midi.object_key == f"{expected_base}/output.mid"
    assert refreshed_xml.object_key == f"{expected_base}/output.musicxml"
    assert refreshed_midi.status == "succeeded"
    assert refreshed_xml.status == "succeeded"

    storage = LocalStorageClient()
    xml_bytes = storage.get_bytes(refreshed_xml.object_key)
    assert xml_bytes.startswith(b"<?xml")
    # Review fix regression: rederive.py's score_json_to_musicxml call must
    # thread the real project title through (same as the transcription
    # export stage) -- without it, every re-derived export reverted to
    # music21's own "Untitled" fallback (musicxml.export._apply_metadata),
    # even though the project's real title ("Riff", set in
    # _arrange_project) was available the whole time.
    assert b"<movement-title>Riff</movement-title>" in xml_bytes
    assert b"Untitled" not in xml_bytes
    midi_bytes = storage.get_bytes(refreshed_midi.object_key)
    assert midi_bytes[:4] == b"MThd"


def test_rederive_over_score_with_silent_measure_succeeds(db_session):
    # A score containing an empty-events measure (silent-measure fidelity)
    # must rederive cleanly end-to-end: DP reassignment tolerates the empty
    # measure, MIDI write skips it without error, and MusicXML export
    # renders it as a whole-bar rest rather than crashing or dropping it.
    ctx = _arrange_project(db_session, score_json=_score_with_silent_measure())
    rederive_job = _make_rederive_job(db_session, ctx["project"], ctx["asset"], input_hash="rederive-silent")

    run_rederive_job(rederive_job.id)
    db_session.expire_all()

    refreshed_job = db_session.get(TranscriptionJob, rederive_job.id)
    assert refreshed_job.status == "succeeded"

    refreshed_revision = db_session.get(ScoreRevision, ctx["revision"].id)
    measures = refreshed_revision.score_json["parts"][0]["measures"]
    assert [m["number"] for m in measures] == [1, 2]
    assert measures[1]["events"] == []  # silent measure preserved, not dropped

    project_id = ctx["project"].id
    expected_base = f"projects/{project_id}/exports/rev0"
    refreshed_xml = db_session.get(Export, ctx["xml_export"].id)
    assert refreshed_xml.object_key == f"{expected_base}/output.musicxml"
    assert refreshed_xml.status == "succeeded"

    storage = LocalStorageClient()
    xml_bytes = storage.get_bytes(refreshed_xml.object_key)
    assert xml_bytes.startswith(b"<?xml")
    assert b'number="2"' in xml_bytes  # measure 2 present in the exported file


def test_rederive_superseded_job_skips(db_session):
    # Arrange: two rederive job rows for the same project with distinct,
    # explicit created_at timestamps (avoids the SQLite same-second tie
    # rederive.py's supersede rule is designed to resolve deterministically).
    ctx = _arrange_project(db_session)
    t_older = datetime(2026, 8, 18, 12, 0, 0)
    t_newer = t_older + timedelta(seconds=5)
    older = _make_rederive_job(
        db_session, ctx["project"], ctx["asset"], input_hash="rederive-old", created_at=t_older,
    )
    newer = _make_rederive_job(
        db_session, ctx["project"], ctx["asset"], input_hash="rederive-new", created_at=t_newer,
    )

    # Act: run the OLDER job first.
    run_rederive_job(older.id)
    db_session.expire_all()

    # Assert: it completes as succeeded WITHOUT touching exports.
    refreshed_older = db_session.get(TranscriptionJob, older.id)
    assert refreshed_older.status == "succeeded"
    assert refreshed_older.progress == 100

    refreshed_midi = db_session.get(Export, ctx["midi_export"].id)
    refreshed_xml = db_session.get(Export, ctx["xml_export"].id)
    assert refreshed_midi.object_key == ctx["original_midi_key"]
    assert refreshed_xml.object_key == ctx["original_xml_key"]

    # Act: now run the NEWER job — it should do the real work.
    run_rederive_job(newer.id)
    db_session.expire_all()

    refreshed_newer = db_session.get(TranscriptionJob, newer.id)
    assert refreshed_newer.status == "succeeded"
    assert refreshed_newer.progress == 100

    project_id = ctx["project"].id
    expected_base = f"projects/{project_id}/exports/rev0"
    refreshed_midi_after = db_session.get(Export, ctx["midi_export"].id)
    refreshed_xml_after = db_session.get(Export, ctx["xml_export"].id)
    assert refreshed_midi_after.object_key == f"{expected_base}/output.mid"
    assert refreshed_xml_after.object_key == f"{expected_base}/output.musicxml"


def test_rederive_export_failure_marks_job_failed_but_keeps_revision(db_session, monkeypatch):
    # Arrange
    ctx = _arrange_project(db_session)
    rederive_job = _make_rederive_job(db_session, ctx["project"], ctx["asset"], input_hash="rederive-1")

    import aura_worker.rederive as rederive_module

    def _boom(score, path, title=None):
        raise RuntimeError("musicxml export exploded")

    monkeypatch.setattr(rederive_module, "score_json_to_musicxml", _boom)

    # Act
    run_rederive_job(rederive_job.id)
    db_session.expire_all()

    # Assert: job failed with error_detail set...
    refreshed_job = db_session.get(TranscriptionJob, rederive_job.id)
    assert refreshed_job.status == "failed"
    assert refreshed_job.error_code == "INTERNAL_ERROR"
    assert refreshed_job.error_detail
    assert "musicxml export exploded" in refreshed_job.error_detail

    # ...but the revision's re-assigned score survives the export failure.
    refreshed_revision = db_session.get(ScoreRevision, ctx["revision"].id)
    events = refreshed_revision.score_json["parts"][0]["measures"][0]["events"]
    locked_event = next(e for e in events if e["id"] == "note_00")
    unlocked_event = next(e for e in events if e["id"] == "note_01")
    assert (locked_event["string"], locked_event["fret"]) == (2, 2)
    assert unlocked_event["string"] is not None  # the DP re-assignment was committed

    # Exports were never touched.
    refreshed_midi = db_session.get(Export, ctx["midi_export"].id)
    assert refreshed_midi.object_key == ctx["original_midi_key"]
