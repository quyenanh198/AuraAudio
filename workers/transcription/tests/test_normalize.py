import wave

from aura_worker.stage_runner import StageContext
from aura_worker.stages import normalize
from test_fixtures.generate import write_guitar_pluck_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def test_normalize_stage_produces_mono_22050hz_wav(db_session, sample_job, workdir):
    source_path = workdir / "source" / "input.wav"
    write_guitar_pluck_wav(source_path, duration_s=1.0, sample_rate=44100)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    normalized_path = normalize.run(ctx, source_path=source_path)

    with wave.open(str(normalized_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 22050

    assert any(key.endswith("normalized.wav") for key in storage.objects)


def test_normalize_stage_second_call_resumes_without_reencoding(db_session, sample_job, workdir, monkeypatch):
    source_path = workdir / "source" / "input.wav"
    write_guitar_pluck_wav(source_path, duration_s=1.0, sample_rate=44100)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    first_path = normalize.run(ctx, source_path=source_path)
    first_bytes = first_path.read_bytes()

    import subprocess

    real_run = subprocess.run

    def fail_if_called(*args, **kwargs):
        raise AssertionError("ffmpeg should not be re-invoked on a cached normalize stage")

    monkeypatch.setattr(subprocess, "run", fail_if_called)
    try:
        second_path = normalize.run(ctx, source_path=source_path)
    finally:
        monkeypatch.setattr(subprocess, "run", real_run)

    assert second_path.read_bytes() == first_bytes
