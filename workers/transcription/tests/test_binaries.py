"""A packaged desktop app cannot assume ffmpeg/ffprobe are on PATH — the
container this was developed in had neither, and both e2e tests failed
with FileNotFoundError until ffmpeg was installed."""
import subprocess
from pathlib import Path
from unittest import mock

from aura_worker.binaries import ffmpeg_path, ffprobe_path
from test_fixtures.generate import write_guitar_pluck_wav


class _FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def test_defaults_to_bare_names_on_path(monkeypatch):
    # Keeps the developer workflow and the test suite working unchanged.
    monkeypatch.delenv("AURA_FFMPEG_PATH", raising=False)
    monkeypatch.delenv("AURA_FFPROBE_PATH", raising=False)

    assert ffmpeg_path() == "ffmpeg"
    assert ffprobe_path() == "ffprobe"


def test_explicit_paths_are_used_when_set(monkeypatch):
    monkeypatch.setenv("AURA_FFMPEG_PATH", "/opt/aura/bin/ffmpeg")
    monkeypatch.setenv("AURA_FFPROBE_PATH", "/opt/aura/bin/ffprobe")

    assert ffmpeg_path() == "/opt/aura/bin/ffmpeg"
    assert ffprobe_path() == "/opt/aura/bin/ffprobe"


def test_empty_value_falls_back_rather_than_producing_an_empty_argv0(monkeypatch):
    monkeypatch.setenv("AURA_FFMPEG_PATH", "")
    monkeypatch.setenv("AURA_FFPROBE_PATH", "   ")

    assert ffmpeg_path() == "ffmpeg"
    assert ffprobe_path() == "ffprobe"


def test_probe_media_invokes_the_resolved_ffprobe(monkeypatch, tmp_path: Path):
    # A test that only covered the resolver would still pass if a call site
    # kept its hardcoded "ffprobe", so assert on the argv actually used.
    monkeypatch.setenv("AURA_FFPROBE_PATH", "/custom/ffprobe")
    media = tmp_path / "a.wav"
    media.write_bytes(b"x")

    from aura_worker import ffmpeg_utils

    with mock.patch.object(ffmpeg_utils.subprocess, "run") as run:
        run.return_value = subprocess.CompletedProcess([], 0, stdout="{}", stderr="")
        try:
            ffmpeg_utils.probe_media(media)
        except Exception:
            pass  # empty json fails validation downstream; argv is what matters

    assert run.call_args[0][0][0] == "/custom/ffprobe"


def test_normalize_invokes_the_resolved_ffmpeg(db_session, sample_job, workdir, monkeypatch):
    # Drive the real stage, not a hand-built argv: a test that only covered
    # the resolver would still pass if this call site kept "ffmpeg".
    monkeypatch.setenv("AURA_FFMPEG_PATH", "/custom/ffmpeg")
    source_path = workdir / "source" / "input.wav"
    write_guitar_pluck_wav(source_path, duration_s=0.5, sample_rate=44100)

    from aura_worker.stage_runner import StageContext
    from aura_worker.stages import normalize

    ctx = StageContext(
        job=sample_job, session=db_session, storage=_FakeStorage(), workdir=workdir
    )

    with mock.patch.object(normalize.subprocess, "run") as run:
        run.side_effect = FileNotFoundError("/custom/ffmpeg")
        try:
            normalize.run(ctx, source_path=source_path)
        except Exception:
            pass  # the binary does not exist; the argv it tried is the point

    assert run.call_args is not None, "normalize.run never shelled out"
    assert run.call_args[0][0][0] == "/custom/ffmpeg"
