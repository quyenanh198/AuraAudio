from aura_api.main import create_app
from aura_worker import binaries
from aura_worker.binaries import ResolvedBinary
from fastapi.testclient import TestClient


def _resolve_on_path(binary: str) -> ResolvedBinary:
    return ResolvedBinary(path=f"/usr/bin/{binary}", source="path")


def test_system_deps_reports_found_binaries_with_versions():
    # ffmpeg/ffprobe are real binaries installed in this test environment,
    # so this exercises the actual resolve_binary + subprocess path end to
    # end. yt-dlp is OPTIONAL and not guaranteed to be on PATH in this
    # environment (it is a guided-install, non-blocking dependency — see
    # routers/system.py), so only its response *shape* is asserted here,
    # not its found value.
    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert set(body.keys()) == {"ffmpeg", "ffprobe", "ytDlp", "allFound"}
    for binary in ("ffmpeg", "ffprobe"):
        assert set(body[binary].keys()) == {"found", "version", "path", "source"}
        assert body[binary]["found"] is True
        assert isinstance(body[binary]["version"], str)
        assert body[binary]["version"] != ""
        assert isinstance(body[binary]["path"], str)
        assert body[binary]["path"] != ""
        assert body[binary]["source"] in {"env", "path", "known_location"}
    assert set(body["ytDlp"].keys()) == {"found", "version", "path", "source"}
    # allFound reflects required deps (ffmpeg + ffprobe) ONLY — yt-dlp is
    # optional and must never affect it, regardless of whether it's present.
    assert body["allFound"] is True


def test_system_deps_reports_missing_when_binaries_not_resolvable(monkeypatch):
    import aura_api.routers.system as system_module

    monkeypatch.setattr(system_module, "resolve_binary", lambda _binary: None)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body == {
        "ffmpeg": {"found": False, "version": None, "path": None, "source": None},
        "ffprobe": {"found": False, "version": None, "path": None, "source": None},
        "ytDlp": {"found": False, "version": None, "path": None, "source": None},
        "allFound": False,
    }


def test_system_deps_reports_resolved_path_and_source(monkeypatch):
    # Proves the path/source fields are threaded through from resolve_binary
    # verbatim, including the "known_location" case (a binary found via one
    # of aura_worker.binaries's well-known per-OS install locations, not
    # plain PATH) -- the case that matters most for the Windows
    # winget-PATH-refresh trap this whole module exists to fix.
    import aura_api.routers.system as system_module

    def _resolve(binary: str) -> ResolvedBinary | None:
        if binary == "ffmpeg":
            return ResolvedBinary(path="/opt/winget-links/ffmpeg.exe", source="known_location")
        return None

    monkeypatch.setattr(system_module, "resolve_binary", _resolve)
    monkeypatch.setattr(system_module.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ffmpeg"]["found"] is True
    assert body["ffmpeg"]["path"] == "/opt/winget-links/ffmpeg.exe"
    assert body["ffmpeg"]["source"] == "known_location"
    assert body["ffprobe"] == {"found": False, "version": None, "path": None, "source": None}


class _FakeCompletedProcess:
    stdout = "ffmpeg version 6.1.1-3ubuntu5 Copyright (c) 2000-2023\n"
    stderr = ""
    returncode = 0


def test_system_deps_handles_unparseable_version_output(monkeypatch):
    import aura_api.routers.system as system_module

    class _UnparseableCompletedProcess:
        stdout = "not a version string at all\n"
        stderr = ""
        returncode = 0

    def _fake_run(*_args, **_kwargs):
        return _UnparseableCompletedProcess()

    monkeypatch.setattr(system_module, "resolve_binary", _resolve_on_path)
    monkeypatch.setattr(system_module.subprocess, "run", _fake_run)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body == {
        "ffmpeg": {"found": True, "version": None, "path": "/usr/bin/ffmpeg", "source": "path"},
        "ffprobe": {"found": True, "version": None, "path": "/usr/bin/ffprobe", "source": "path"},
        # yt-dlp's real `--version` output is a bare version string (e.g.
        # "2024.08.06"), not the "<binary> version X" shape ffmpeg uses, so
        # it's parsed separately (_parse_yt_dlp_version) against a
        # YYYY.MM.DD-shaped pattern. Garbage output fails that pattern too.
        "ytDlp": {"found": True, "version": None, "path": "/usr/bin/yt-dlp", "source": "path"},
        "allFound": True,
    }


def test_system_deps_never_crashes_when_subprocess_raises(monkeypatch):
    import subprocess

    import aura_api.routers.system as system_module

    def _raise(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=5)

    monkeypatch.setattr(system_module, "resolve_binary", _resolve_on_path)
    monkeypatch.setattr(system_module.subprocess, "run", _raise)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ffmpeg"] == {
        "found": True, "version": None, "path": "/usr/bin/ffmpeg", "source": "path",
    }
    assert body["ffprobe"] == {
        "found": True, "version": None, "path": "/usr/bin/ffprobe", "source": "path",
    }
    assert body["ytDlp"] == {
        "found": True, "version": None, "path": "/usr/bin/yt-dlp", "source": "path",
    }
    assert body["allFound"] is True


def test_system_deps_parses_real_shaped_yt_dlp_version(monkeypatch):
    import aura_api.routers.system as system_module

    class _FakeYtDlpVersionProcess:
        stdout = "2024.08.06\n"
        stderr = ""
        returncode = 0

    def _fake_run(*_args, **_kwargs):
        return _FakeYtDlpVersionProcess()

    monkeypatch.setattr(system_module, "resolve_binary", _resolve_on_path)
    monkeypatch.setattr(system_module.subprocess, "run", _fake_run)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ytDlp"]["found"] is True
    assert body["ytDlp"]["version"] == "2024.08.06"
    # Still doesn't affect allFound even though yt-dlp is now "found".
    assert body["allFound"] is True


def test_system_deps_never_500s_when_resolve_binary_raises(monkeypatch):
    # v1.2.1 regression: `resolve_binary` is documented to never raise, but
    # a real-Windows filesystem probe broke that contract (see
    # aura_worker.binaries's own regression tests) and both endpoints that
    # call it bare-500'd. This is the router-level belt-and-braces: even if
    # `resolve_binary` itself is ever broken again, this endpoint must
    # degrade to a diagnosable `found: false`, never a bare 500.
    import aura_api.routers.system as system_module

    def _raise(_binary: str):
        raise OSError(1920, "The file cannot be accessed by the system")

    monkeypatch.setattr(system_module, "resolve_binary", _raise)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "ffmpeg": {"found": False, "version": None, "path": None, "source": None},
        "ffprobe": {"found": False, "version": None, "path": None, "source": None},
        "ytDlp": {"found": False, "version": None, "path": None, "source": None},
        "allFound": False,
    }


def test_system_deps_yt_dlp_missing_never_blocks_all_found(monkeypatch):
    # CRITICAL invariant: allFound means "required deps (ffmpeg+ffprobe)
    # present" — yt-dlp being absent must never flip it to False and must
    # never trigger the blocking banner on the frontend.
    import aura_api.routers.system as system_module

    def _resolve(binary: str) -> ResolvedBinary | None:
        return None if binary == "yt-dlp" else _resolve_on_path(binary)

    monkeypatch.setattr(system_module, "resolve_binary", _resolve)
    monkeypatch.setattr(system_module.subprocess, "run", lambda *a, **k: _FakeCompletedProcess())

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    body = resp.json()
    assert body["ytDlp"] == {"found": False, "version": None, "path": None, "source": None}
    assert body["ffmpeg"]["found"] is True
    assert body["ffprobe"]["found"] is True
    assert body["allFound"] is True


def test_system_deps_version_checks_pass_windows_creationflags_on_win32(monkeypatch):
    """Windows hidden-console audit: the ffmpeg/ffprobe/yt-dlp `-version`
    subprocess calls this endpoint makes must splat
    `aura_worker.binaries.subprocess_flags()`. `subprocess.run` is fully
    faked (not just wrapped) so `sys.platform` can be forced to `"win32"`
    (via `aura_worker.binaries.sys`) without also handing a Windows-only
    `creationflags` kwarg to this suite's real POSIX `subprocess.Popen`,
    which would raise."""
    import aura_api.routers.system as system_module

    monkeypatch.setattr(binaries.sys, "platform", "win32")
    monkeypatch.setattr(system_module, "resolve_binary", _resolve_on_path)

    captured_calls: list[dict] = []

    def _fake_run(*_args, **kwargs):
        captured_calls.append(kwargs)
        return _FakeCompletedProcess()

    monkeypatch.setattr(system_module.subprocess, "run", _fake_run)

    client = TestClient(create_app())
    resp = client.get("/v1/system/deps")
    assert resp.status_code == 200

    # ffmpeg + ffprobe + yt-dlp -- one `-version`/`--version` call each.
    assert len(captured_calls) == 3
    assert all(kwargs.get("creationflags") == 0x0800_0000 for kwargs in captured_calls)
