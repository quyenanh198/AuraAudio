import shutil

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import probe
from test_fixtures.generate import write_guitar_pluck_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]

    def download_media_asset(self, object_key: str, dest):
        raise NotImplementedError


def test_probe_stage_updates_media_asset_and_returns_info(db_session, sample_job, workdir, monkeypatch):
    fixture_path = workdir / "source.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0)

    storage = FakeStorage()

    def fake_download(object_key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_path, dest)
        return dest

    monkeypatch.setattr(storage, "download_media_asset", fake_download)

    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)
    info = probe.run(ctx)

    assert info.sample_rate == 44100
    from aura_api.models import MediaAsset

    refreshed = db_session.get(MediaAsset, sample_job.media_asset_id)
    assert refreshed.sha256 is not None
    assert refreshed.duration_ms is not None


def test_probe_stage_rejects_media_exceeding_duration_limit(db_session, sample_job, workdir, monkeypatch):
    fixture_path = workdir / "source.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0)

    storage = FakeStorage()

    def fake_download(object_key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_path, dest)
        return dest

    monkeypatch.setattr(storage, "download_media_asset", fake_download)
    monkeypatch.setattr("aura_worker.stages.probe.MAX_DURATION_MS", 1000)

    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)
    try:
        probe.run(ctx)
        assert False, "expected JobFailure"
    except JobFailure as exc:
        assert exc.code.value == "MEDIA_TOO_LARGE"


def test_probe_stage_second_call_resumes_without_redownloading(db_session, sample_job, workdir, monkeypatch):
    fixture_path = workdir / "source.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0)

    storage = FakeStorage()
    download_calls = {"count": 0}

    def fake_download(object_key, dest):
        download_calls["count"] += 1
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fixture_path, dest)
        return dest

    monkeypatch.setattr(storage, "download_media_asset", fake_download)

    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)
    first_info = probe.run(ctx)
    second_info = probe.run(ctx)

    assert download_calls["count"] == 1  # second call resumed from the cached artifact
    assert first_info == second_info
