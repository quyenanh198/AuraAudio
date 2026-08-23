"""Runner-level tests for bug 3 (stage/progress display).

These monkeypatch each stage module's `run` (the same names
aura_worker.runner imports and calls) with fast fakes that don't touch
ffmpeg/basic-pitch/librosa, so the pipeline's STAGE WIRING can be verified
in isolation from the real (slow, dependency-heavy) stage implementations
-- see test_stage_runner.py for the lower-level start_stage unit tests this
builds on. Each fake calls save_artifact itself (exactly as its real
counterpart does), so progress actually advances through the pipeline the
same way it would for real.
"""
from __future__ import annotations

from aura_worker.ffmpeg_utils import ProbeInfo
from aura_worker.runner import run_transcription_job
from aura_worker.stage_runner import STAGE_PROGRESS, save_artifact
from aura_worker.stages.structure import StructureResult
from score_schema.models import NoteEvent, build_score


def _fake_score(instrument: str) -> dict:
    return build_score(
        instrument=instrument,
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 1.0, "meter": 1.0, "key": 1.0},
        time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_00",
                        "pitch": 60,
                        "onsetSeconds": 0.0,
                        "offsetSeconds": 0.5,
                        "notatedOnset": "0/4",
                        "notatedDuration": "1/4",
                        "voice": 1,
                        "confidence": 1.0,
                        "locked": False,
                    }
                ],
            }
        ],
    )


def _patch_common_stages(monkeypatch, stage_seen: dict, workdir_note_path, instrument: str):
    """Patches probe/normalize/structure/quantize/export -- the stages
    common to every instrument -- to fast fakes that (a) record job.stage/
    job.progress at call time, exactly what a polling client would see
    while that stage is the one actually executing, and (b) call
    save_artifact themselves (like the real stage modules do) so progress
    genuinely advances between stages."""
    import aura_worker.runner as runner_module

    def _record(ctx, name):
        stage_seen[name] = (ctx.job.stage, ctx.job.progress)

    def fake_probe_run(ctx):
        _record(ctx, "probe")
        save_artifact(ctx, "probe", 1, object_key="jobs/x/probe.json", sha256="h-probe", metrics={})
        return ProbeInfo(container="wav", codec="pcm_s16le", duration_ms=4000, sample_rate=22050)

    def fake_normalize_run(ctx, source_path):
        _record(ctx, "normalize")
        workdir_note_path.touch()
        save_artifact(ctx, "normalize", 1, object_key="jobs/x/normalized.wav", sha256="h-norm", metrics={})
        return workdir_note_path

    def fake_structure_run(ctx, normalized_path, notes):
        _record(ctx, "structure")
        save_artifact(ctx, "structure", 1, object_key="jobs/x/structure.json", sha256="h-struct", metrics={})
        return StructureResult(
            tempo_bpm=120.0, meter="4/4", key="C major",
            tempo_confidence=1.0, meter_confidence=1.0, key_confidence=1.0,
        )

    def fake_quantize_run(ctx, notes, structure):
        _record(ctx, "quantize")
        save_artifact(ctx, "quantize", 1, object_key="jobs/x/score.json", sha256="h-quant", metrics={})
        return _fake_score(instrument)

    def fake_export_run(ctx, notes, score):
        _record(ctx, "export")
        ctx.job.status = "succeeded"
        ctx.job.stage = "export"
        ctx.job.progress = STAGE_PROGRESS["export"]
        ctx.session.commit()
        return {"midi_key": "k1", "musicxml_key": "k2"}

    monkeypatch.setattr(runner_module.probe, "run", fake_probe_run)
    monkeypatch.setattr(runner_module.normalize, "run", fake_normalize_run)
    monkeypatch.setattr(runner_module.structure, "run", fake_structure_run)
    monkeypatch.setattr(runner_module.quantize, "run", fake_quantize_run)
    monkeypatch.setattr(runner_module.export_stage, "run", fake_export_run)


def test_stage_shows_inference_not_stale_normalize_while_inference_runs(
    monkeypatch, db_session, sample_job, tmp_path
):
    """Direct regression test for the reported bug: while the (slow) piano
    inference stage is executing, the job row must already say
    "inference" -- not sit on "normalize" (the previous stage) for the
    entire duration."""
    sample_job.project.instrument = "piano"
    db_session.commit()

    stage_seen: dict[str, tuple[str | None, int]] = {}
    _patch_common_stages(monkeypatch, stage_seen, tmp_path / "normalized.wav", instrument="piano")

    import aura_worker.runner as runner_module

    def fake_inference_run(ctx, normalized_path):
        # THE bug-3 assertion: at the moment inference is doing its (real,
        # slow) work, the job row must already reflect "inference" as the
        # current stage -- this is what a polling client would see for the
        # full duration of the CRNN call. Before the fix, job.stage was
        # still "normalize" (the last stage save_artifact had touched) at
        # this exact point.
        assert ctx.job.stage == "inference", (
            f"expected job.stage == 'inference' while inference is running, got {ctx.job.stage!r}"
        )
        assert ctx.job.progress == STAGE_PROGRESS["normalize"], (
            "expected progress to sit at the previous (completed) stage's end value "
            f"while inference runs, got {ctx.job.progress}"
        )
        stage_seen["inference"] = (ctx.job.stage, ctx.job.progress)
        save_artifact(ctx, "inference", 1, object_key="jobs/x/notes.json", sha256="h-inf", metrics={})
        return [NoteEvent(pitch=60, onset_s=0.0, offset_s=0.5, velocity=100, confidence=1.0)]

    monkeypatch.setattr(runner_module.inference, "run", fake_inference_run)
    monkeypatch.setattr(runner_module, "_SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    run_transcription_job(sample_job.id)

    # start_stage("probe") already ran before probe.run was called -- that
    # is the fix: job.stage reflects the stage about to execute, not None
    # or a stale value, from the very first stage on.
    assert stage_seen["probe"] == ("probe", 0)
    assert stage_seen["normalize"] == ("normalize", STAGE_PROGRESS["probe"])
    assert stage_seen["inference"] == ("inference", STAGE_PROGRESS["normalize"])
    assert stage_seen["structure"] == ("structure", STAGE_PROGRESS["inference"])
    assert stage_seen["quantize"] == ("quantize", STAGE_PROGRESS["structure"])
    # assign is NOT patched in this test -- the real assign.run executes
    # (piano hand assignment) and advances progress to its own end value
    # before export starts.
    assert stage_seen["export"] == ("export", STAGE_PROGRESS["assign"])

    job = db_session.get(type(sample_job), sample_job.id)
    assert job.status == "succeeded"
    assert job.stage == "export"
    assert job.progress == 100


def test_full_pipeline_stage_progress_is_monotonic_including_separate(monkeypatch, db_session, sample_job, tmp_path):
    """Guitar project with separateSource enabled -- the optional `separate`
    stage must appear in the sequence, and each stage must see job.stage
    already set to its OWN name (not a stale earlier one), with progress
    never decreasing across the whole run."""
    sample_job.project.instrument = "guitar"
    sample_job.project.settings = {"separateSource": True}
    db_session.commit()

    stage_seen: dict[str, tuple[str | None, int]] = {}
    _patch_common_stages(monkeypatch, stage_seen, tmp_path / "normalized.wav", instrument="guitar")

    import aura_worker.runner as runner_module

    def fake_separate_run(ctx, source_path):
        stage_seen["separate"] = (ctx.job.stage, ctx.job.progress)
        save_artifact(ctx, "separate", 1, object_key="jobs/x/separated.wav", sha256="h-sep", metrics={})
        return source_path

    def fake_inference_run(ctx, normalized_path):
        stage_seen["inference"] = (ctx.job.stage, ctx.job.progress)
        save_artifact(ctx, "inference", 1, object_key="jobs/x/notes.json", sha256="h-inf", metrics={})
        return [NoteEvent(pitch=60, onset_s=0.0, offset_s=0.5, velocity=100, confidence=1.0)]

    def fake_assign_run(ctx, score):
        stage_seen["assign"] = (ctx.job.stage, ctx.job.progress)
        save_artifact(ctx, "assign", 1, object_key="jobs/x/assign.json", sha256="h-assign", metrics={})
        return score

    monkeypatch.setattr(runner_module.separate, "run", fake_separate_run)
    monkeypatch.setattr(runner_module.inference, "run", fake_inference_run)
    monkeypatch.setattr(runner_module.assign, "run", fake_assign_run)
    monkeypatch.setattr(runner_module, "_SessionLocal", lambda: db_session)
    monkeypatch.setattr(db_session, "close", lambda: None)

    run_transcription_job(sample_job.id)

    assert "separate" in stage_seen, "separate stage must run (and be observed) when separateSource is set"

    ordered_stages = ["probe", "separate", "normalize", "inference", "structure", "quantize", "assign", "export"]
    assert list(stage_seen.keys()) == ordered_stages, "stages must run, and be observed, in pipeline order"

    progresses = [stage_seen[s][1] for s in ordered_stages]
    assert progresses == sorted(progresses), f"progress must be monotonic, got {progresses}"

    stages_named = [stage_seen[s][0] for s in ordered_stages]
    assert stages_named == ordered_stages, (
        "each stage must see job.stage already set to ITS OWN name at call time, not a stale previous one -- "
        f"got {stages_named}"
    )

    # separate's own STAGE_PROGRESS entry must actually be reachable in the
    # sequence (bug-3 checklist item: `separate` covered by the map at all).
    assert stage_seen["normalize"][1] == STAGE_PROGRESS["separate"]
