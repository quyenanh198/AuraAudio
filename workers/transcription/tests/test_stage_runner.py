from aura_worker.stage_runner import STAGE_PROGRESS, StageContext, find_cached_artifact, save_artifact, start_stage


def test_start_stage_sets_stage_name_immediately(db_session, sample_job, workdir):
    """Bug 3: job.stage must reflect the stage that is ABOUT to run, not
    the last one that finished -- this is what start_stage is for."""
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    start_stage(ctx, "inference")

    assert sample_job.stage == "inference"


def test_start_stage_keeps_previous_stages_end_progress(db_session, sample_job, workdir):
    """While inference is running, the displayed percent should be
    whatever normalize's completion already set it to (25) -- accurate
    ("25% of the pipeline is done, currently on inference"), not
    inference's own end value (that would falsely claim inference is
    already done)."""
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)
    save_artifact(ctx, "normalize", 1, object_key="jobs/x/normalized.wav", sha256="abc", metrics={})
    assert sample_job.progress == STAGE_PROGRESS["normalize"]

    start_stage(ctx, "inference")

    assert sample_job.stage == "inference"
    assert sample_job.progress == STAGE_PROGRESS["normalize"]


def test_start_stage_first_stage_gets_zero_progress(db_session, sample_job, workdir):
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    start_stage(ctx, "probe")

    assert sample_job.stage == "probe"
    assert sample_job.progress == 0


def test_start_stage_never_decreases_progress(db_session, sample_job, workdir):
    """Regression guard for the monotonicity requirement: start_stage must
    never lower job.progress below what a completed stage already set --
    even a stage name earlier in the fixed pipeline order (e.g. a resumed
    job re-entering "normalize" bookkeeping after inference already
    finished) must not walk progress backward."""
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)
    save_artifact(ctx, "inference", 1, object_key="jobs/x/notes.json", sha256="abc", metrics={})
    assert sample_job.progress == STAGE_PROGRESS["inference"]

    start_stage(ctx, "normalize")  # earlier in pipeline order than inference

    assert sample_job.progress == STAGE_PROGRESS["inference"]


def test_start_stage_then_save_artifact_is_monotonic_across_full_pipeline(db_session, sample_job, workdir):
    """Full stage_runner-level walk through the pipeline order, asserting
    progress never decreases at any point -- the ordering property the
    fix promises end to end, independent of aura_worker.runner's own
    wiring."""
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)
    stage_order = ["probe", "separate", "normalize", "inference", "structure", "quantize", "assign", "export"]

    seen_progress: list[int] = []
    for i, stage in enumerate(stage_order):
        start_stage(ctx, stage)
        seen_progress.append(sample_job.progress)
        assert sample_job.stage == stage
        save_artifact(ctx, stage, 1, object_key=f"jobs/x/{stage}.bin", sha256=f"h{i}", metrics={})
        seen_progress.append(sample_job.progress)

    assert seen_progress == sorted(seen_progress)


def test_separate_stage_is_covered_by_progress_map():
    # Bug 3 checklist item: `separate` (guitar source-separation, opt-in)
    # must be a real entry in STAGE_PROGRESS, not silently missing.
    assert "separate" in STAGE_PROGRESS


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
