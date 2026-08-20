from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aura_api.main import create_app

VALID_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "http://youtube.com/watch?v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
]

INVALID_URLS = [
    # userinfo trick: parsed hostname is "evil.com", not "youtube.com"
    "https://youtube.com@evil.com/watch?v=x",
    # non-YouTube host entirely
    "https://evil.com/watch?v=x",
    # suffix trick: hostname is "youtube.com.evil.com"
    "https://youtube.com.evil.com/watch?v=x",
    # prefix trick / substring in path, not the host
    "https://evil.com/youtube.com/watch?v=x",
    # disallowed scheme
    "ftp://youtube.com/watch?v=x",
    "javascript:alert(1)",
    # empty
    "",
    # not a URL at all
    "not a url",
]


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "settings", config.settings)
    monkeypatch.setattr(imports_module, "storage_client", storage.storage_client)
    return TestClient(create_app())


@pytest.mark.parametrize("url", VALID_URLS)
def test_valid_youtube_urls_pass_validation(url, client, monkeypatch):
    # Force the yt-dlp-missing branch so we isolate URL validation from the
    # download path: a valid URL must reach the 409 (yt-dlp missing), never
    # the 422 (URL rejected).
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module.shutil, "which", lambda _binary: None)
    resp = client.post("/v1/imports/youtube", json={"url": url})
    assert resp.status_code == 409, f"{url!r} should pass validation, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("url", INVALID_URLS)
def test_invalid_youtube_urls_rejected_with_422(url, client):
    resp = client.post("/v1/imports/youtube", json={"url": url})
    assert resp.status_code == 422, f"{url!r} should be rejected, got {resp.status_code}: {resp.text}"
    assert "detail" in resp.json()


def test_yt_dlp_missing_returns_409_with_machine_readable_detail(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module.shutil, "which", lambda _binary: None)
    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "yt_dlp_not_found"
    assert "message" in detail


def _fake_which(binary: str) -> str | None:
    return "/usr/bin/yt-dlp" if binary == "yt-dlp" else None


def _find_output_dir(cmd: list[str]) -> Path:
    o_index = cmd.index("-o")
    template = cmd[o_index + 1]
    # template is "<tmp_dir>/%(id)s.%(ext)s"
    return Path(template).parent


def test_success_path_registers_upload_and_returns_shape_compatible_response(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module.shutil, "which", _fake_which)

    captured_cmd: dict[str, list[str]] = {}

    class _FakeCompletedProcess:
        def __init__(self, returncode: int, stdout: str, stderr: str) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    def _fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        out_dir = _find_output_dir(cmd)
        # Simulate yt-dlp's real -x/--audio-format mp3 output: a single mp3
        # file named after the video id.
        (out_dir / "dQw4w9WgXcQ.mp3").write_bytes(b"ID3-fake-mp3-bytes")
        stdout = (
            f"{imports_module._TITLE_MARKER}Never Gonna Give You Up\n"
            "[youtube] dQw4w9WgXcQ: Downloading webpage\n"
            "[ExtractAudio] Destination: dQw4w9WgXcQ.mp3\n"
        )
        return _FakeCompletedProcess(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201, resp.text
    body = resp.json()

    # Shape-compatible with POST /v1/uploads's CreateUploadResponse: the
    # frontend must be able to read `object_key` the same way either way.
    assert body["object_key"].startswith("uploads/")
    assert body["object_key"].endswith("/dQw4w9WgXcQ.mp3")
    assert body["title"] == "Never Gonna Give You Up"

    # Registered through the SAME storage path POST /v1/uploads uses.
    from aura_api import storage

    assert storage.storage_client.get_bytes(body["object_key"]) == b"ID3-fake-mp3-bytes"

    # argv list, never a shell string.
    assert isinstance(captured_cmd["cmd"], list)
    assert captured_cmd["cmd"][0] == "/usr/bin/yt-dlp"
    assert VALID_URLS[0] in captured_cmd["cmd"]

    # Temp dir must be cleaned up afterward.
    out_dir = _find_output_dir(captured_cmd["cmd"])
    assert not out_dir.exists()


def test_success_path_without_title_marker_omits_title(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module.shutil, "which", _fake_which)

    class _FakeCompletedProcess:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    def _fake_run(cmd, **kwargs):
        out_dir = _find_output_dir(cmd)
        (out_dir / "abc123.mp3").write_bytes(b"bytes")
        return _FakeCompletedProcess(stdout="[youtube] abc123: Downloading webpage\n")

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201
    assert resp.json()["title"] is None


def test_nonzero_exit_returns_502_with_truncated_stderr_tail(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module.shutil, "which", _fake_which)

    class _FakeCompletedProcess:
        returncode = 1
        stdout = ""

        def __init__(self, stderr: str) -> None:
            self.stderr = stderr

    long_stderr = "ERROR: " + ("x" * 500)

    def _fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(stderr=long_stderr)

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert len(detail) < len(long_stderr)
    assert detail.endswith("x" * 50)  # tail, not head, is preserved


def test_timeout_returns_502(client, monkeypatch):
    import subprocess

    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module.shutil, "which", _fake_which)

    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=300)

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 502
    assert "timed out" in resp.json()["detail"]


def test_no_output_file_produced_returns_502(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module.shutil, "which", _fake_which)

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        # yt-dlp "succeeds" but writes nothing -- shouldn't happen for real,
        # but the handler must not crash on an empty tmp dir.
        return _FakeCompletedProcess()

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 502
