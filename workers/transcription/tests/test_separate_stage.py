from __future__ import annotations

import wave

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import separate
from test_fixtures.generate import write_guitar_pluck_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def test_separate_stage_produces_cached_artifact(db_session, sample_job, workdir):
    """Real end-to-end run against the real, build-time-fetched demucs
    weights -- short clip, same reasoning as test_separation.py's real
    test for why this isn't mocked away."""
    source_path = workdir / "source" / "input.wav"
    write_guitar_pluck_wav(source_path, duration_s=1.0, sample_rate=44100)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    out_path = separate.run(ctx, source_path=source_path)

    with wave.open(str(out_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
    assert any(key.endswith("separated.wav") for key in storage.objects)


def test_separate_stage_second_call_resumes_from_cache(db_session, sample_job, workdir, monkeypatch):
    source_path = workdir / "source" / "input.wav"
    write_guitar_pluck_wav(source_path, duration_s=1.0, sample_rate=44100)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    first_path = separate.run(ctx, source_path=source_path)
    first_bytes = first_path.read_bytes()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("separate_guitar should not be re-invoked on a cached separate stage")

    monkeypatch.setattr("aura_worker.stages.separate.separate_guitar", fail_if_called)
    second_path = separate.run(ctx, source_path=source_path)

    assert second_path.read_bytes() == first_bytes


def test_separate_stage_wraps_failures_as_job_failure(db_session, sample_job, workdir, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated model failure")

    monkeypatch.setattr("aura_worker.stages.separate.separate_guitar", boom)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    try:
        separate.run(ctx, source_path=workdir / "does-not-matter.wav")
        raise AssertionError("expected JobFailure")
    except JobFailure as exc:
        assert "source separation failed" in exc.detail
