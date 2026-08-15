from aura_api.models import MediaAsset, Project, TranscriptionJob


def test_creating_a_project_and_job(db_session):
    project = Project(owner_id="user_1", title="My Riff", instrument="guitar")
    db_session.add(project)
    db_session.flush()

    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/riff.wav")
    db_session.add(asset)
    db_session.flush()

    job = TranscriptionJob(
        project_id=project.id, media_asset_id=asset.id, input_hash="abc123"
    )
    db_session.add(job)
    db_session.commit()

    assert job.status == "created"
    assert job.project_id == project.id


def test_duplicate_input_hash_per_project_is_rejected(db_session):
    import pytest
    from sqlalchemy.exc import IntegrityError

    project = Project(owner_id="user_1", title="My Riff", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/riff.wav")
    db_session.add(asset)
    db_session.flush()

    db_session.add(TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="dup"))
    db_session.commit()

    db_session.add(TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="dup"))
    with pytest.raises(IntegrityError):
        db_session.commit()
