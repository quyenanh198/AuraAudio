"""A sidecar killed mid-job leaves its row at "running" with no process
anywhere intending to finish it. On a server that is rare; on a desktop
app users close windows routinely."""
import pytest
from aura_api.models import MediaAsset, Project, TranscriptionJob
from aura_api.startup import recover_interrupted_jobs


def _job(session, status: str, input_hash: str) -> TranscriptionJob:
    project = Project(owner_id="local", title="t", instrument="guitar")
    session.add(project)
    session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key=f"k/{input_hash}", bytes=1)
    session.add(asset)
    session.flush()
    job = TranscriptionJob(
        project_id=project.id,
        media_asset_id=asset.id,
        status=status,
        input_hash=input_hash,
    )
    session.add(job)
    session.commit()
    return job


def test_running_job_is_failed(db_session):
    job = _job(db_session, "running", "h_running")

    recover_interrupted_jobs(db_session)

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_code == "INTERNAL_ERROR"
    assert job.error_detail is not None and job.error_detail.strip()


@pytest.mark.parametrize("status", ["pending", "created", "succeeded", "failed"])
def test_other_statuses_are_left_alone(db_session, status):
    # The assertion that matters: a recovery that also clobbered pending or
    # created jobs would silently discard queued work, and a test covering
    # only the running row would not notice.
    job = _job(db_session, status, f"h_{status}")

    recover_interrupted_jobs(db_session)

    db_session.refresh(job)
    assert job.status == status
    assert job.error_code is None


def test_recovers_every_running_job_not_just_the_first(db_session):
    a = _job(db_session, "running", "h_a")
    b = _job(db_session, "running", "h_b")

    recover_interrupted_jobs(db_session)

    db_session.refresh(a)
    db_session.refresh(b)
    assert (a.status, b.status) == ("failed", "failed")


def test_is_a_no_op_when_nothing_was_interrupted(db_session):
    job = _job(db_session, "succeeded", "h_clean")

    recover_interrupted_jobs(db_session)

    db_session.refresh(job)
    assert job.status == "succeeded"
