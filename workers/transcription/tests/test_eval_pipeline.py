from aura_worker.eval.pipeline import PipelineResult, run_pipeline_stages
from test_fixtures.generate import write_guitar_pluck_wav


def test_run_pipeline_stages_runs_real_stages_end_to_end(tmp_path, workdir):
    wav_path = tmp_path / "riff.wav"
    write_guitar_pluck_wav(wav_path, duration_s=2.0, sample_rate=44100)

    result = run_pipeline_stages(wav_path, instrument="guitar", workdir=workdir)

    assert isinstance(result, PipelineResult)
    assert len(result.notes) > 0
    assert result.tempo_bpm > 0
    assert result.meter in {"4/4", "3/4", "6/8", "2/4"}
    assert isinstance(result.key, str) and " " in result.key
    assert result.score["schemaVersion"] == 4


def test_run_pipeline_stages_independent_runs_do_not_collide(tmp_path, workdir):
    # Two separate fixtures run back-to-back must each get their own
    # Project/Job rows (no id/state bleed from a shared engine).
    wav_a = tmp_path / "a.wav"
    wav_b = tmp_path / "b.wav"
    write_guitar_pluck_wav(wav_a, duration_s=2.0, sample_rate=44100)
    write_guitar_pluck_wav(wav_b, duration_s=2.0, sample_rate=44100)

    workdir_a, workdir_b = workdir / "a", workdir / "b"
    workdir_a.mkdir()
    workdir_b.mkdir()

    result_a = run_pipeline_stages(wav_a, instrument="guitar", workdir=workdir_a)
    result_b = run_pipeline_stages(wav_b, instrument="piano", workdir=workdir_b)

    assert result_a.score["parts"][0]["instrument"] == "guitar"
    assert result_b.score["parts"][0]["instrument"] == "piano"
