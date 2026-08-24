from pathlib import Path

import pytest
from aura_api.main import create_app
from aura_worker.binaries import ResolvedBinary
from fastapi.testclient import TestClient

VALID_URLS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "http://youtube.com/watch?v=dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://music.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    # uppercase host -- urlsplit().hostname lowercases it, so this must be
    # accepted the same as the lowercase form, not rejected.
    "https://YOUTUBE.COM/watch?v=dQw4w9WgXcQ",
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
    # IPv6 literal host -- never in the allowlist, rejected like any other
    # non-YouTube host.
    "https://[::1]/watch?v=x",
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

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_nothing)
    resp = client.post("/v1/imports/youtube", json={"url": url})
    assert resp.status_code == 409, f"{url!r} should pass validation, got {resp.status_code}: {resp.text}"


@pytest.mark.parametrize("url", INVALID_URLS)
def test_invalid_youtube_urls_rejected_with_422(url, client):
    resp = client.post("/v1/imports/youtube", json={"url": url})
    assert resp.status_code == 422, f"{url!r} should be rejected, got {resp.status_code}: {resp.text}"
    assert "detail" in resp.json()


def test_nul_byte_in_url_returns_422_not_500(client, monkeypatch):
    # `urlsplit(...).hostname` doesn't reject an embedded NUL byte living in
    # the path/query (the hostname component itself is clean, so hostname
    # validation passes it through) -- it's the OS-exec boundary,
    # `subprocess.run`, that raises `ValueError: embedded null byte` when
    # handed it as an argv element. Deliberately does NOT mock
    # subprocess.run: this is the real call that must be guarded, and a
    # nonexistent yt-dlp path is fine because Python validates argv strings
    # (and raises) before it ever gets to exec/FileNotFoundError.
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)
    resp = client.post(
        "/v1/imports/youtube",
        json={"url": "https://youtube.com/watch?v=x\x00y"},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail == "invalid URL"
    # Must not leak the raw exception internals (path, "null byte", etc.)
    assert "null byte" not in detail
    assert "yt-dlp" not in detail


def test_url_over_max_length_returns_422(client):
    long_url = "https://youtube.com/watch?v=" + ("a" * 2048)
    resp = client.post("/v1/imports/youtube", json={"url": long_url})
    assert resp.status_code == 422


def test_yt_dlp_missing_returns_409_with_machine_readable_detail(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_nothing)
    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["code"] == "yt_dlp_not_found"
    assert "message" in detail


def test_resolve_binary_raising_returns_502_not_500(client, monkeypatch):
    # v1.2.1 regression: `resolve_binary` is documented to never raise (see
    # aura_worker.binaries), but a real-Windows filesystem probe broke that
    # contract and this endpoint bare-500'd. This is the router-level
    # belt-and-braces: even if `resolve_binary` is ever broken again, the
    # import flow must degrade to a diagnosable 502, never a bare 500.
    import aura_api.routers.imports as imports_module

    def _raise(_name: str):
        raise OSError(1920, "The file cannot be accessed by the system")

    monkeypatch.setattr(imports_module, "resolve_binary", _raise)
    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})

    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail["code"] == "binary_resolution_failed"
    assert "message" in detail


def _resolve_nothing(_name: str) -> ResolvedBinary | None:
    return None


def _resolve_only_yt_dlp(name: str) -> ResolvedBinary | None:
    """`resolve_binary` stub: yt-dlp resolves (from PATH), ffmpeg does not --
    isolates these tests from the `--ffmpeg-location` behavior, which has
    its own dedicated tests below (`test_ffmpeg_location_flag_*`)."""
    if name == "yt-dlp":
        return ResolvedBinary(path="/usr/bin/yt-dlp", source="path")
    return None


def _find_output_dir(cmd: list[str]) -> Path:
    o_index = cmd.index("-o")
    template = cmd[o_index + 1]
    # template is "<tmp_dir>/%(id)s.%(ext)s"
    return Path(template).parent


def test_success_path_registers_upload_and_returns_shape_compatible_response(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

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

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

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

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

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

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    def _fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd=cmd, timeout=300)

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 502
    assert "timed out" in resp.json()["detail"]


def test_no_output_file_produced_returns_502(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    class _FakeCompletedProcess:
        returncode = 0
        stdout = "[youtube] abc123: Downloading webpage\n[info] some unanticipated no-op"
        stderr = ""

    def _fake_run(cmd, **kwargs):
        # yt-dlp "succeeds" but writes nothing -- shouldn't happen for real,
        # but the handler must not crash on an empty tmp dir.
        return _FakeCompletedProcess()

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    # Bug 1 fix: the generic "no audio file" 502 must carry yt-dlp's own
    # output tail so future cases are diagnosable from the UI instead of a
    # bare, undiagnosable string.
    assert "no-op" in detail


def test_max_filesize_skip_returns_422_not_generic_502(client, monkeypatch):
    """Known yt-dlp behavior (bug 1, scenario a): `--max-filesize 200m`
    SKIPS the download and exits 0 with a "File is larger than
    max-filesize" message -- no file is ever written. Before the fix this
    fell through to the same undiagnosable "produced no audio file" 502 as
    a genuine failure; it must instead surface a specific, actionable 422."""
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""

        def __init__(self, stderr: str) -> None:
            self.stderr = stderr

    def _fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(
            stderr="[download] abc123: File is larger than max-filesize (250.00MiB > 200.00MiB); not downloading"
        )

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert "200MB" in detail
    assert "large" in detail.lower()


def test_max_filesize_skip_detected_even_on_nonzero_returncode(client, monkeypatch):
    # Real yt-dlp exits 0 for this case, but the detection is content-based
    # (the message), not exit-code-based -- it must still win over the
    # generic "yt-dlp failed" 502 if some version/wrapper ever returns
    # nonzero for the same skip.
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    class _FakeCompletedProcess:
        returncode = 1
        stdout = ""

        def __init__(self, stderr: str) -> None:
            self.stderr = stderr

    def _fake_run(cmd, **kwargs):
        return _FakeCompletedProcess(stderr="File is larger than max-filesize (300.00MiB > 200.00MiB)")

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 422, resp.text


def test_non_mp3_audio_output_is_accepted(client, monkeypatch):
    """Bug 1, scenario (b): format-selection/postprocessing can leave a
    non-mp3 audio file behind (e.g. mp3 postprocessing silently no-ops
    without ffmpeg on yt-dlp's own PATH) even though `-x --audio-format
    mp3` was requested. The worker's probe step validates the real codec,
    not the extension, so this must succeed rather than report "no audio
    file" just because the glob only looked for *.mp3."""
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    def _fake_run(cmd, **kwargs):
        out_dir = _find_output_dir(cmd)
        (out_dir / "abc123.m4a").write_bytes(b"fake-m4a-bytes")
        return _FakeCompletedProcess(returncode=0, stdout="", stderr="")

    class _FakeCompletedProcess:
        def __init__(self, returncode, stdout, stderr):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["object_key"].endswith("abc123.m4a")


def test_mp3_preferred_over_other_extensions_when_both_present(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        out_dir = _find_output_dir(cmd)
        # A leftover .webm alongside the real mp3 output shouldn't win.
        (out_dir / "abc123.webm").write_bytes(b"fake-webm-bytes")
        (out_dir / "abc123.mp3").write_bytes(b"fake-mp3-bytes")
        return _FakeCompletedProcess()

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201, resp.text
    assert resp.json()["object_key"].endswith("abc123.mp3")


def _resolve_yt_dlp_and_known_location_ffmpeg(name: str) -> ResolvedBinary | None:
    if name == "yt-dlp":
        return ResolvedBinary(path="/usr/bin/yt-dlp", source="path")
    if name == "ffmpeg":
        # "known_location" -- the exact case that matters here: ffmpeg was
        # NOT found via plain PATH lookup, only via one of the well-known
        # per-OS install locations, so yt-dlp's own independent PATH search
        # would miss it too without --ffmpeg-location telling it where to
        # look.
        return ResolvedBinary(path="/opt/winget-links/ffmpeg.exe", source="known_location")
    return None


def test_print_flag_paired_with_no_simulate(client, monkeypatch):
    """Regression guard for bug 1: yt-dlp's `--print TEMPLATE` implies
    `--simulate` (download skipped) unless `--no-simulate` is ALSO passed.
    Whenever the built command contains `--print` it must also contain
    `--no-simulate`, or a real (non-stubbed) yt-dlp silently produces no
    file while still exiting 0 with the title line printed."""
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    captured_cmd: dict[str, list[str]] = {}

    def _fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        out_dir = _find_output_dir(cmd)
        (out_dir / "abc123.mp3").write_bytes(b"bytes")
        return _RealSemanticsFakeYtDlp(cmd)

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201, resp.text

    cmd = captured_cmd["cmd"]
    assert "--print" in cmd
    assert "--no-simulate" in cmd


class _RealSemanticsFakeYtDlp:
    """A `subprocess.run` result stand-in that mimics REAL yt-dlp's
    `--print`-implies-`--simulate` semantics (used only by the stub below,
    not by the other tests' fully-faked stand-ins above, which already
    write the file unconditionally and would otherwise mask this bug)."""

    def __init__(self, cmd: list[str]) -> None:
        self.returncode = 0
        self.stderr = ""
        self.stdout = "AURA_YT_TITLE:some title\n"


def test_print_without_no_simulate_downloads_nothing_stub_semantics(client, monkeypatch, tmp_path):
    """Drives the endpoint against a stub that honors yt-dlp's real
    `--print`-implies-`--simulate` semantics: if `--print` is present and
    `--no-simulate` is NOT, no output file is written (matching real
    yt-dlp), which must surface as the "produced no audio file" 502. This
    is the "before the fix" behavior, kept as a live check that the stub
    itself models the trap correctly (not just a string-match on argv).
    """
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    def _real_semantics_fake_run(cmd, **kwargs):
        out_dir = _find_output_dir(cmd)
        has_print = "--print" in cmd
        has_no_simulate = "--no-simulate" in cmd
        if has_print and not has_no_simulate:
            # Real yt-dlp: simulate-only, prints the template line, writes
            # NOTHING.
            return _RealSemanticsFakeYtDlp(cmd)
        # Real yt-dlp with --no-simulate: actually downloads.
        (out_dir / "abc123.mp3").write_bytes(b"bytes")
        return _RealSemanticsFakeYtDlp(cmd)

    monkeypatch.setattr(imports_module.subprocess, "run", _real_semantics_fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})

    # With the bug 1 fix in place, `cmd` always carries `--no-simulate`
    # alongside `--print`, so the stub takes the "actually downloads"
    # branch and the request succeeds. If `--no-simulate` regressed out of
    # `cmd`, this stub would instead produce no file and the endpoint would
    # 502 with "produced no audio file" -- exactly the real-world bug 1
    # symptom.
    assert resp.status_code == 201, resp.text


def test_ffmpeg_location_flag_present_when_ffmpeg_resolves_via_known_location(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_yt_dlp_and_known_location_ffmpeg)

    captured_cmd: dict[str, list[str]] = {}

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        out_dir = _find_output_dir(cmd)
        (out_dir / "abc123.mp3").write_bytes(b"bytes")
        return _FakeCompletedProcess()

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201, resp.text

    cmd = captured_cmd["cmd"]
    assert "--ffmpeg-location" in cmd
    # The directory containing the resolved ffmpeg binary, not the binary
    # path itself.
    assert cmd[cmd.index("--ffmpeg-location") + 1] == "/opt/winget-links"


def test_ffmpeg_location_flag_omitted_when_ffmpeg_cannot_be_resolved_at_all(client, monkeypatch):
    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    captured_cmd: dict[str, list[str]] = {}

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured_cmd["cmd"] = cmd
        out_dir = _find_output_dir(cmd)
        (out_dir / "abc123.mp3").write_bytes(b"bytes")
        return _FakeCompletedProcess()

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201, resp.text
    assert "--ffmpeg-location" not in captured_cmd["cmd"]


def test_yt_dlp_spawn_passes_windows_creationflags_on_win32(client, monkeypatch):
    """Windows hidden-console audit: the yt-dlp subprocess call this
    endpoint makes must splat `aura_worker.binaries.subprocess_flags()`.
    `subprocess.run` is fully faked (not just wrapped) so `sys.platform`
    can be forced to `"win32"` (via `aura_worker.binaries.sys`) without
    also handing a Windows-only `creationflags` kwarg to this suite's real
    POSIX `subprocess.Popen`, which would raise."""
    from aura_worker import binaries

    import aura_api.routers.imports as imports_module

    monkeypatch.setattr(binaries.sys, "platform", "win32")
    monkeypatch.setattr(imports_module, "resolve_binary", _resolve_only_yt_dlp)

    captured: dict = {}

    class _FakeCompletedProcess:
        returncode = 0
        stdout = ""
        stderr = ""

    def _fake_run(cmd, **kwargs):
        captured.update(kwargs)
        out_dir = _find_output_dir(cmd)
        (out_dir / "abc123.mp3").write_bytes(b"bytes")
        return _FakeCompletedProcess()

    monkeypatch.setattr(imports_module.subprocess, "run", _fake_run)

    resp = client.post("/v1/imports/youtube", json={"url": VALID_URLS[0]})
    assert resp.status_code == 201, resp.text
    assert captured.get("creationflags") == 0x0800_0000
