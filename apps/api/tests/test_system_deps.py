from aura_api.main import create_app
from fastapi.testclient import TestClient


def test_system_deps_reports_found_binaries_with_versions():
    # ffmpeg/ffprobe are real binaries installed in this test environment,
    # so this exercises the actual shutil.which + subprocess path end to end.
    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"ffmpeg", "ffprobe", "allFound"}
    for binary in ("ffmpeg", "ffprobe"):
        assert set(body[binary].keys()) == {"found", "version"}
        assert body[binary]["found"] is True
        assert isinstance(body[binary]["version"], str)
        assert body[binary]["version"] != ""
    assert body["allFound"] is True


def test_system_deps_reports_missing_when_binaries_not_on_path(monkeypatch):
    import aura_api.routers.system as system_module

    monkeypatch.setattr(system_module.shutil, "which", lambda _binary: None)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body == {
        "ffmpeg": {"found": False, "version": None},
        "ffprobe": {"found": False, "version": None},
        "allFound": False,
    }


def test_system_deps_handles_unparseable_version_output(monkeypatch):
    import aura_api.routers.system as system_module

    class _FakeCompletedProcess:
        stdout = "not a version string at all\n"
        stderr = ""
        returncode = 0

    def _fake_run(*_args, **_kwargs):
        return _FakeCompletedProcess()

    monkeypatch.setattr(system_module.shutil, "which", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setattr(system_module.subprocess, "run", _fake_run)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body == {
        "ffmpeg": {"found": True, "version": None},
        "ffprobe": {"found": True, "version": None},
        "allFound": True,
    }


def test_system_deps_never_crashes_when_subprocess_raises(monkeypatch):
    import subprocess

    import aura_api.routers.system as system_module

    def _raise(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)

    monkeypatch.setattr(system_module.shutil, "which", lambda binary: f"/usr/bin/{binary}")
    monkeypatch.setattr(system_module.subprocess, "run", _raise)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ffmpeg"] == {"found": True, "version": None}
    assert body["ffprobe"] == {"found": True, "version": None}
    assert body["allFound"] is True
