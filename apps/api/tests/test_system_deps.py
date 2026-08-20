from aura_api.main import create_app
from fastapi.testclient import TestClient


def test_system_deps_reports_found_binaries_with_versions():
    # ffmpeg/ffprobe are real binaries installed in this test environment,
    # so this exercises the actual shutil.which + subprocess path end to end.
    # yt-dlp is OPTIONAL and not guaranteed to be on PATH in this environment
    # (it is a guided-install, non-blocking dependency — see routers/system.py),
    # so only its response *shape* is asserted here, not its found value.
    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"ffmpeg", "ffprobe", "ytDlp", "allFound"}
    for binary in ("ffmpeg", "ffprobe"):
        assert set(body[binary].keys()) == {"found", "version"}
        assert body[binary]["found"] is True
        assert isinstance(body[binary]["version"], str)
        assert body[binary]["version"] != ""
    assert set(body["ytDlp"].keys()) == {"found", "version"}
    # allFound reflects required deps (ffmpeg + ffprobe) ONLY — yt-dlp is
    # optional and must never affect it, regardless of whether it's present.
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
        "ytDlp": {"found": False, "version": None},
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
        # yt-dlp's real `--version` output is a bare version string (e.g.
        # "2024.08.06"), not the "<binary> version X" shape ffmpeg uses, so
        # it's parsed separately (_parse_yt_dlp_version) against a
        # YYYY.MM.DD-shaped pattern. Garbage output fails that pattern too.
        "ytDlp": {"found": True, "version": None},
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
    assert body["ytDlp"] == {"found": True, "version": None}
    assert body["allFound"] is True


def test_system_deps_parses_real_shaped_yt_dlp_version(monkeypatch):
    import aura_api.routers.system as system_module

    class _FakeCompletedProcess:
        stdout = "2024.08.06\n"
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
    assert body["ytDlp"] == {"found": True, "version": "2024.08.06"}
    # Still doesn't affect allFound even though yt-dlp is now "found".
    assert body["allFound"] is True


def test_system_deps_yt_dlp_missing_never_blocks_all_found(monkeypatch):
    # CRITICAL invariant: allFound means "required deps (ffmpeg+ffprobe)
    # present" — yt-dlp being absent must never flip it to False and must
    # never trigger the blocking banner on the frontend.
    import aura_api.routers.system as system_module

    def _which(binary: str) -> str | None:
        return None if binary == "yt-dlp" else f"/usr/bin/{binary}"

    class _FakeCompletedProcess:
        stdout = "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023\n"
        stderr = ""
        returncode = 0

    monkeypatch.setattr(system_module.shutil, "which", _which)
    monkeypatch.setattr(system_module.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ytDlp"] == {"found": False, "version": None}
    assert body["ffmpeg"]["found"] is True
    assert body["ffprobe"]["found"] is True
    assert body["allFound"] is True
