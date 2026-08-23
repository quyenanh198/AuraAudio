"""Covers `aura_worker.binaries.resolve_binary`'s full resolution order:
env override > PATH > known per-OS install locations > not found.

Known-location cases build a REAL file under `tmp_path` and point the
relevant env var (`LOCALAPPDATA`, `HOME`, etc.) at it, rather than
monkeypatching `pathlib.Path` methods -- this exercises the actual
filesystem check `resolve_binary` performs, not a mocked stand-in for it.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess

import pytest
from aura_worker import binaries
from aura_worker.binaries import ResolvedBinary


@pytest.fixture(autouse=True)
def _clear_env_overrides(monkeypatch):
    # None of these tests want a real dev machine's env vars (if any happen
    # to be set) leaking in and short-circuiting the resolution order under
    # test.
    for env_var in binaries._ENV_OVERRIDES.values():
        monkeypatch.delenv(env_var, raising=False)
    # `resolve_binary` mutates the REAL process `PATH` on a known-location
    # hit (see `_ensure_on_process_path`) -- by design, not a bug (that's
    # how it fixes third-party bare-name shellouts like demucs's). Every
    # test below that exercises a known-location hit would otherwise leak
    # that mutation into the actual test-process environment and pollute
    # later tests/files. Snapshotting PATH via `monkeypatch.setenv` here
    # (even though its value doesn't change yet) makes monkeypatch the
    # owner of restoring it, regardless of how the code under test mutates
    # it afterward -- not just the tests that explicitly set PATH
    # themselves.
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))


def test_env_override_wins_even_when_on_path(monkeypatch, tmp_path):
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: "/usr/bin/ffmpeg")
    override_path = tmp_path / "custom-ffmpeg"
    monkeypatch.setenv("AURA_FFMPEG_PATH", str(override_path))

    resolved = binaries.resolve_binary("ffmpeg")

    assert resolved == ResolvedBinary(path=str(override_path), source=binaries.ENV_OVERRIDE)


def test_env_override_not_required_to_exist_on_disk(monkeypatch, tmp_path):
    # An explicit override is trusted outright -- resolve_binary doesn't
    # stat it. A bad override should fail loudly at the real subprocess
    # call site, not silently fall through to search here.
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("AURA_YT_DLP_PATH", str(missing))

    resolved = binaries.resolve_binary("yt-dlp")

    assert resolved == ResolvedBinary(path=str(missing), source=binaries.ENV_OVERRIDE)


def test_empty_env_override_falls_through_to_path(monkeypatch):
    monkeypatch.setenv("AURA_FFMPEG_PATH", "")
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    resolved = binaries.resolve_binary("ffmpeg")

    assert resolved == ResolvedBinary(path="/usr/bin/ffmpeg", source=binaries.ON_PATH)


def test_path_hit_wins_over_known_locations(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: "/opt/custom/ffprobe")

    resolved = binaries.resolve_binary("ffprobe")

    assert resolved == ResolvedBinary(path="/opt/custom/ffprobe", source=binaries.ON_PATH)


def test_unknown_binary_name_has_no_env_override_but_can_still_search_path(monkeypatch):
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: "/usr/bin/whatever")

    resolved = binaries.resolve_binary("some-other-tool")

    assert resolved == ResolvedBinary(path="/usr/bin/whatever", source=binaries.ON_PATH)


def test_both_miss_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)
    monkeypatch.setattr(binaries.platform, "system", lambda: "Linux")
    # Point every known Linux location this module checks at empty,
    # real-but-harmless directories under tmp_path so nothing is
    # accidentally found on the machine actually running this test.
    monkeypatch.setattr(binaries.Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.setattr(
        binaries,
        "_linux_locations",
        lambda name: [tmp_path / "usr-bin" / name, tmp_path / "usr-local-bin" / name],
    )

    assert binaries.resolve_binary("ffmpeg") is None


class TestWindowsKnownLocations:
    """`platform.system` is forced to "Windows" so these run the Windows
    branch on any host OS (CI runs Linux)."""

    @pytest.fixture(autouse=True)
    def _force_windows(self, monkeypatch):
        monkeypatch.setattr(binaries.platform, "system", lambda: "Windows")
        monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)

    def test_winget_links_dir_hit(self, monkeypatch, tmp_path):
        local_app_data = tmp_path / "LocalAppData"
        links_dir = local_app_data / "Microsoft" / "WinGet" / "Links"
        links_dir.mkdir(parents=True)
        exe_path = links_dir / "ffmpeg.exe"
        exe_path.write_bytes(b"stub")
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved == ResolvedBinary(path=str(exe_path), source=binaries.KNOWN_LOCATION)

    def test_winget_packages_glob_hit_when_links_dir_absent(self, monkeypatch, tmp_path):
        local_app_data = tmp_path / "LocalAppData"
        package_bin = (
            local_app_data
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "ffmpeg-7.1-full_build"
            / "bin"
        )
        package_bin.mkdir(parents=True)
        exe_path = package_bin / "ffmpeg.exe"
        exe_path.write_bytes(b"stub")
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved == ResolvedBinary(path=str(exe_path), source=binaries.KNOWN_LOCATION)

    def test_links_dir_checked_before_packages_glob(self, monkeypatch, tmp_path):
        local_app_data = tmp_path / "LocalAppData"
        links_dir = local_app_data / "Microsoft" / "WinGet" / "Links"
        links_dir.mkdir(parents=True)
        links_exe = links_dir / "ffmpeg.exe"
        links_exe.write_bytes(b"stub")

        package_bin = (
            local_app_data
            / "Microsoft"
            / "WinGet"
            / "Packages"
            / "Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
            / "ffmpeg-7.1-full_build"
            / "bin"
        )
        package_bin.mkdir(parents=True)
        (package_bin / "ffmpeg.exe").write_bytes(b"stub")

        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved is not None
        assert resolved.path == str(links_exe)

    def test_program_files_ffmpeg_bin_hit(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        program_files = tmp_path / "Program Files"
        bin_dir = program_files / "ffmpeg" / "bin"
        bin_dir.mkdir(parents=True)
        exe_path = bin_dir / "ffmpeg.exe"
        exe_path.write_bytes(b"stub")
        monkeypatch.setenv("ProgramFiles", str(program_files))

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved == ResolvedBinary(path=str(exe_path), source=binaries.KNOWN_LOCATION)

    def test_chocolatey_bin_hit(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("ProgramFiles", raising=False)
        program_data = tmp_path / "ProgramData"
        choco_bin = program_data / "chocolatey" / "bin"
        choco_bin.mkdir(parents=True)
        exe_path = choco_bin / "yt-dlp.exe"
        exe_path.write_bytes(b"stub")
        monkeypatch.setenv("ProgramData", str(program_data))

        resolved = binaries.resolve_binary("yt-dlp")

        assert resolved == ResolvedBinary(path=str(exe_path), source=binaries.KNOWN_LOCATION)

    def test_no_known_location_present_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        monkeypatch.delenv("ProgramFiles", raising=False)
        monkeypatch.delenv("ProgramData", raising=False)

        assert binaries.resolve_binary("ffmpeg") is None


class TestMacosKnownLocations:
    @pytest.fixture(autouse=True)
    def _force_macos(self, monkeypatch):
        monkeypatch.setattr(binaries.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)

    def test_homebrew_apple_silicon_prefix_hit(self, monkeypatch, tmp_path):
        homebrew_bin = tmp_path / "opt-homebrew-bin"
        homebrew_bin.mkdir()
        exe_path = homebrew_bin / "ffmpeg"
        exe_path.write_bytes(b"stub")
        monkeypatch.setattr(
            binaries,
            "_macos_locations",
            lambda name: [homebrew_bin / name, tmp_path / "usr-local-bin" / name],
        )

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved == ResolvedBinary(path=str(exe_path), source=binaries.KNOWN_LOCATION)


class TestLinuxKnownLocations:
    @pytest.fixture(autouse=True)
    def _force_linux(self, monkeypatch):
        monkeypatch.setattr(binaries.platform, "system", lambda: "Linux")
        monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)

    def test_home_local_bin_hit(self, monkeypatch, tmp_path):
        fake_home = tmp_path / "home" / "user"
        local_bin = fake_home / ".local" / "bin"
        local_bin.mkdir(parents=True)
        exe_path = local_bin / "yt-dlp"
        exe_path.write_bytes(b"stub")
        monkeypatch.setattr(binaries.Path, "home", staticmethod(lambda: fake_home))
        monkeypatch.setattr(
            binaries,
            "_linux_locations",
            lambda name: [
                tmp_path / "usr-bin" / name,
                tmp_path / "usr-local-bin" / name,
                binaries.Path.home() / ".local" / "bin" / name,
            ],
        )

        resolved = binaries.resolve_binary("yt-dlp")

        assert resolved == ResolvedBinary(path=str(exe_path), source=binaries.KNOWN_LOCATION)


def test_real_system_platform_dispatch_smoke():
    """Not mocked: proves `_known_locations` dispatches to the branch
    matching the REAL host OS pytest is running on, without raising."""
    locations = binaries._known_locations("ffmpeg")
    assert isinstance(locations, list)
    if platform.system() == "Linux":
        assert any(str(p).endswith("/usr/bin/ffmpeg") for p in locations)


class TestKnownLocationPathPrepend:
    """Covers the demucs fix: a known-location hit must prepend its
    directory to this process's real `PATH`, exactly once no matter how
    many times it's re-resolved, and that mutation must be visible to
    third-party code doing its own independent bare-name PATH lookup in
    the same process (demucs's `AudioFile.read()` shells out to a
    hardcoded `["ffmpeg", ...]`, invisible to `resolve_binary` itself)."""

    @pytest.fixture(autouse=True)
    def _force_linux_known_location_only(self, monkeypatch):
        monkeypatch.setattr(binaries.platform, "system", lambda: "Linux")
        monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)

    def test_known_location_hit_prepends_its_directory_to_process_path(self, monkeypatch, tmp_path):
        bin_dir = tmp_path / "usr-bin"
        bin_dir.mkdir()
        (bin_dir / "ffmpeg").write_bytes(b"stub")
        monkeypatch.setattr(binaries, "_linux_locations", lambda name: [bin_dir / name])
        original_path = os.environ["PATH"]

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved is not None
        assert resolved.source == binaries.KNOWN_LOCATION
        assert os.environ["PATH"] == f"{bin_dir}{os.pathsep}{original_path}"

    def test_prepend_is_idempotent_across_repeated_resolves(self, monkeypatch, tmp_path):
        bin_dir = tmp_path / "usr-bin"
        bin_dir.mkdir()
        (bin_dir / "ffmpeg").write_bytes(b"stub")
        monkeypatch.setattr(binaries, "_linux_locations", lambda name: [bin_dir / name])

        binaries.resolve_binary("ffmpeg")
        binaries.resolve_binary("ffmpeg")
        binaries.resolve_binary("ffmpeg")

        entries = os.environ["PATH"].split(os.pathsep)
        assert entries.count(str(bin_dir)) == 1

    def test_prepend_does_not_duplicate_when_path_is_empty(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PATH", "")
        bin_dir = tmp_path / "usr-bin"
        bin_dir.mkdir()
        (bin_dir / "ffmpeg").write_bytes(b"stub")
        monkeypatch.setattr(binaries, "_linux_locations", lambda name: [bin_dir / name])

        binaries.resolve_binary("ffmpeg")

        assert os.environ["PATH"] == str(bin_dir)


class TestWindowsCrashRegression:
    """v1.2.1 regression: BOTH `GET /v1/system/deps` and
    `POST /v1/imports/youtube` returned bare 500s on real Windows machines
    even though `resolve_binary` is documented (and, in v1.2.0's simpler
    `shutil.which`-only implementation, was structurally guaranteed) to
    never raise. Root cause: `pathlib.Path.is_file`/`is_dir`/`glob` only
    swallow a narrow, allowlisted set of OSErrors internally (see
    `pathlib._ignore_error`) -- notably NOT `PermissionError`, and NOT the
    OneDrive-placeholder-style `OSError` real corporate Windows machines
    can hit when `%LOCALAPPDATA%` is cloud-redirected. These tests force
    exactly those raises out of the underlying `pathlib` calls (impossible
    to trigger for real off a Windows box) and prove `resolve_binary`
    degrades to "not found" instead of propagating.
    """

    @pytest.fixture(autouse=True)
    def _force_windows(self, monkeypatch):
        monkeypatch.setattr(binaries.platform, "system", lambda: "Windows")
        monkeypatch.setattr(binaries.shutil, "which", lambda _name: None)

    def test_permission_error_on_links_dir_stat_does_not_crash(self, monkeypatch, tmp_path):
        # A WinGet Links shim that's a reparse point this process's token
        # can list the parent of, but can't stat directly (ACL-restricted /
        # AV-quarantined) -- Path.is_file() raises PermissionError here,
        # unswallowed by pathlib itself.
        local_app_data = tmp_path / "LocalAppData"
        (local_app_data / "Microsoft" / "WinGet" / "Links").mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

        from pathlib import Path as RealPath

        real_is_file = RealPath.is_file

        def _raising_is_file(self):
            if self.name == "ffmpeg.exe":
                raise PermissionError(13, "Access is denied")
            return real_is_file(self)

        monkeypatch.setattr(binaries.Path, "is_file", _raising_is_file)

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved is None

    def test_onedrive_style_oserror_during_glob_does_not_crash(self, monkeypatch, tmp_path):
        # A cloud-redirected %LOCALAPPDATA% (OneDrive "Known Folder Move")
        # can fail MID-WALK with an OSError that ISN'T PermissionError and
        # ISN'T in pathlib's narrow ignore-list (e.g. WinError 1920) -- real
        # pathlib glob() is a LAZY GENERATOR (walks the tree incrementally,
        # not "get everything or fail immediately"), so the faithful shape
        # of this failure is "found some real matches, THEN the walk hits
        # an inaccessible entry deeper in the tree" -- not a call that fails
        # before glob() has produced anything at all (review fix: a plain
        # function that raises synchronously, the previous version of this
        # test, can never actually exercise `_safe_glob`'s
        # `sorted(root.glob(pattern))` consuming a PARTIALLY-successful
        # generator, only a call that fails before `glob()` even starts
        # returning). This version is itself a generator: it yields one
        # REAL match (a genuine ffmpeg build the walk found first) before
        # raising on its next iteration.
        local_app_data = tmp_path / "LocalAppData"
        packages_root = local_app_data / "Microsoft" / "WinGet" / "Packages"
        good_build_bin = packages_root / "Gyan.FFmpeg_first_hit" / "bin"
        good_build_bin.mkdir(parents=True)
        good_exe = good_build_bin / "ffmpeg.exe"
        good_exe.write_bytes(b"stub")
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

        def _raising_glob(self, _pattern):
            yield good_exe  # one real match, found before the failure
            raise OSError(1920, "The file cannot be accessed by the system")

        monkeypatch.setattr(binaries.Path, "glob", _raising_glob)

        resolved = binaries.resolve_binary("ffmpeg")

        # sorted(root.glob(pattern)) (_safe_glob) must fully consume the
        # generator before it can return anything -- a failure partway
        # through means even the real match already yielded is discarded,
        # not just the ones that would have come after the failure. This
        # is still the correct, safe outcome (never found is honest; it
        # would be a worse bug to silently return a truncated/inconsistent
        # partial result), so `resolved` is still None here, same as the
        # fail-immediately version of this test.
        assert resolved is None

    def test_permission_error_from_is_dir_guard_does_not_crash(self, monkeypatch, tmp_path):
        local_app_data = tmp_path / "LocalAppData"
        (local_app_data / "Microsoft" / "WinGet").mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

        from pathlib import Path as RealPath

        real_is_dir = RealPath.is_dir

        def _raising_is_dir(self):
            if self.name == "Packages":
                raise PermissionError(13, "Access is denied")
            return real_is_dir(self)

        monkeypatch.setattr(binaries.Path, "is_dir", _raising_is_dir)

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved is None

    def test_unexpected_exception_anywhere_in_known_locations_falls_through(self, monkeypatch):
        # Belt-and-braces: even a completely unanticipated failure inside
        # `_known_locations` itself (not just the specific pathlib probes
        # above) must degrade to "not found", not propagate -- this is the
        # top-level `try`/`except` in `resolve_binary`, not the targeted
        # `_safe_*` helpers.
        def _boom(_name):
            raise RuntimeError("simulated unforeseen failure")

        monkeypatch.setattr(binaries, "_known_locations", _boom)

        assert binaries.resolve_binary("ffmpeg") is None

    def test_permission_error_still_resolves_other_candidates(self, monkeypatch, tmp_path):
        # A crash on ONE candidate must not stop resolution from finding a
        # LATER, healthy candidate -- proves this degrades gracefully
        # rather than just "fails safe by giving up everything".
        local_app_data = tmp_path / "LocalAppData"
        (local_app_data / "Microsoft" / "WinGet" / "Links").mkdir(parents=True)
        monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
        program_files = tmp_path / "Program Files"
        bin_dir = program_files / "ffmpeg" / "bin"
        bin_dir.mkdir(parents=True)
        healthy_exe = bin_dir / "ffmpeg.exe"
        healthy_exe.write_bytes(b"stub")
        monkeypatch.setenv("ProgramFiles", str(program_files))

        from pathlib import Path as RealPath

        real_is_file = RealPath.is_file

        def _raising_is_file(self):
            # The Links dir candidate raises; the healthy ProgramFiles
            # candidate (checked after it) must still resolve.
            if "WinGet" in str(self):
                raise PermissionError(13, "Access is denied")
            return real_is_file(self)

        monkeypatch.setattr(binaries.Path, "is_file", _raising_is_file)

        resolved = binaries.resolve_binary("ffmpeg")

        assert resolved == ResolvedBinary(path=str(healthy_exe), source=binaries.KNOWN_LOCATION)


def test_known_location_prepend_is_visible_to_a_separate_stage(monkeypatch, tmp_path):
    # Deliberately OUTSIDE `TestKnownLocationPathPrepend`: that class's
    # autouse fixture patches `shutil.which` for the test's ENTIRE
    # duration via the (function-scoped) `monkeypatch` fixture, so a
    # nested scoped patch reverting mid-test would still land back on
    # THAT patched lambda, not the real function. This test needs
    # `shutil.which` patched ONLY around the `resolve_binary` call, so it
    # sets up its own platform/PATH state directly and uses
    # `pytest.MonkeyPatch.context()` (which reverts to whatever was
    # active immediately before it, i.e. the real `shutil.which`, since
    # nothing else in this test's own scope patched it first).
    #
    # Stands in for a SEPARATE call site in the same process (e.g.
    # demucs's own internal subprocess lookup, or a later pipeline stage)
    # doing its own bare-name PATH lookup with the REAL (unpatched)
    # `shutil.which` -- proving the PATH mutation is visible process-wide,
    # not just to aura_worker's own code path.
    monkeypatch.setattr(binaries.platform, "system", lambda: "Linux")
    bin_dir = tmp_path / "usr-bin"
    bin_dir.mkdir()
    exe = bin_dir / "ffmpeg"
    exe.write_bytes(b"#!/bin/sh\n")
    exe.chmod(0o755)
    monkeypatch.setattr(binaries, "_linux_locations", lambda name: [bin_dir / name])

    with pytest.MonkeyPatch.context() as scoped:
        scoped.setattr(binaries.shutil, "which", lambda _name: None)
        resolved = binaries.resolve_binary("ffmpeg")

    assert resolved is not None
    assert resolved.source == binaries.KNOWN_LOCATION
    assert shutil.which("ffmpeg") == str(exe)


class TestSubprocessFlags:
    """`subprocess_flags()` -- the shared, platform-correct kwargs every
    call site in this app splats into its `subprocess.run` calls so a
    packaged Windows build never flashes a console window for a child
    process (see backend.rs's `--noconsole`/`CREATE_NO_WINDOW` work for the
    parent app itself, and this function's own doc comment for why every
    CHILD process needs its own opt-out too)."""

    def test_returns_empty_dict_on_non_windows(self, monkeypatch):
        monkeypatch.setattr(binaries.sys, "platform", "linux")
        assert binaries.subprocess_flags() == {}

    def test_returns_empty_dict_on_macos(self, monkeypatch):
        monkeypatch.setattr(binaries.sys, "platform", "darwin")
        assert binaries.subprocess_flags() == {}

    def test_returns_create_no_window_creationflags_on_win32(self, monkeypatch):
        monkeypatch.setattr(binaries.sys, "platform", "win32")
        assert binaries.subprocess_flags() == {"creationflags": 0x0800_0000}

    def test_never_references_the_real_stdlib_attribute_off_windows(self):
        """`subprocess.CREATE_NO_WINDOW` genuinely does not exist as an
        attribute on `subprocess` outside real Windows Python -- this test
        runs on Linux (like the rest of this suite), so if `binaries`
        referenced that name anywhere (import time or call time) instead of
        keeping its own hardcoded `_CREATE_NO_WINDOW` copy, importing
        `binaries` above, or the `win32`-forced test just before this one,
        would already have raised `AttributeError`. Asserting the module's
        own constant matches the real flag's known value pins that it's a
        real, always-defined literal, not a lazy read of the (here,
        nonexistent) stdlib attribute."""
        assert not hasattr(subprocess, "CREATE_NO_WINDOW")
        assert binaries._CREATE_NO_WINDOW == 0x0800_0000


