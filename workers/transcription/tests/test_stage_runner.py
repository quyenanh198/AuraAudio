from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact


def test_find_cached_artifact_is_none_until_saved(db_session, sample_job, workdir):
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    assert find_cached_artifact(ctx, "probe", 1) is None

    save_artifact(ctx, "probe", 1, object_key="jobs/x/probe.json", sha256="deadbeef", metrics={"duration_ms": 2000})

    found = find_cached_artifact(ctx, "probe", 1)
    assert found is not None
    assert found.object_key == "jobs/x/probe.json"
    assert found.sha256 == "deadbeef"


def test_save_artifact_advances_job_stage_and_progress(db_session, sample_job, workdir):
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    save_artifact(ctx, "inference", 1, object_key="jobs/x/notes.json", sha256="cafebabe", metrics={})

    assert sample_job.stage == "inference"
    assert sample_job.progress == 55


def test_find_cached_artifact_is_scoped_to_stage_and_version(db_session, sample_job, workdir):
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    save_artifact(ctx, "probe", 1, object_key="jobs/x/probe.json", sha256="deadbeef", metrics={})

    assert find_cached_artifact(ctx, "probe", 2) is None
    assert find_cached_artifact(ctx, "normalize", 1) is None
