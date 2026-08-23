"""Robust, cross-platform resolution of the external binaries this app
shells out to (`ffmpeg`, `ffprobe`, `yt-dlp`).

Why this exists (see docs/superpowers/SESSION-HANDOFF.md for the full
diagnosis): a bare `shutil.which("ffmpeg")` is enough on a dev machine
where PATH already includes it, but real user reports (YouTube import's
"audio-extraction error", transcription failing for BOTH instruments) all
traced back to the SAME root cause on Windows -- the app process's PATH
doesn't include the install directory a package manager just used, either
because that manager (winget, in particular) writes to a stable-but-
non-PATH `Links` directory, or because PATH itself was only refreshed for
NEW processes started after the install, not the app process already
running. `probe_media`/`normalize` (ffmpeg_utils.py, stages/normalize.py)
and the YouTube importer (routers/imports.py) all shell out to a binary by
bare name today, so all three symptoms share one fix: look in the well-known
per-OS install locations those package managers actually use, IN ADDITION
TO plain PATH, before giving up.

Resolution order (first hit wins), matching `resolve_binary`'s docstring:
1. An explicit env var override (`AURA_FFMPEG_PATH` / `AURA_FFPROBE_PATH` /
   `AURA_YT_DLP_PATH`), if set -- lets an advanced user or a packaged build
   pin an exact path, bypassing search entirely.
2. `shutil.which(name)` -- ordinary PATH lookup, unchanged from before.
3. A fixed list of well-known per-OS install locations (see
   `_known_locations`), checked in order, first existing file wins.

A known-location hit additionally PREPENDS that directory to this
process's own `os.environ["PATH"]` (see `_ensure_on_process_path`).
This isn't just cosmetic: this app doesn't own every ffmpeg call site --
demucs's own `AudioFile.read()` (used by `aura_worker.separation`, guitar
source-separation) shells out with a hardcoded bare `["ffmpeg", ...]`
via its OWN `subprocess` call, doing its own independent PATH lookup that
`resolve_binary` has no visibility into or control over. Without this
mutation, a Windows user who fixed ffmpeg-via-winget for probe/normalize
(this module's own call sites) would still hit demucs's internal failure
the moment they enabled "Isolate instrument from mix" -- same root
disease, a call site this module can't wrap. Mutating the process's real
PATH is the only fix that reaches code outside this module's control.

This module intentionally has ZERO dependency on `aura_api` -- `aura-worker`
is a dependency of `aura-api` (see workers/transcription/pyproject.toml),
not the other way around in the general case (the one existing exception,
`aura_worker.stages.probe` importing `aura_api.models.MediaAsset`, is a
narrow, pre-existing exception this module does not need to follow), so
placing the shared resolver here -- rather than duplicating it in
`apps/api` -- lets `aura_api.routers.system` import it directly without
introducing a new circular or backward dependency.
"""

from __future__ import annotations

import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path

#: Binary name -> environment variable that, if set to a non-empty value,
#: is trusted as the exact path to that binary without any further checks
#: (not even that the path exists -- an explicit override is assumed to be
#: correct; failing loudly at the actual subprocess call site is more
#: useful than silently falling through to search here).
_ENV_OVERRIDES: dict[str, str] = {
    "ffmpeg": "AURA_FFMPEG_PATH",
    "ffprobe": "AURA_FFPROBE_PATH",
    "yt-dlp": "AURA_YT_DLP_PATH",
}

#: How a `ResolvedBinary` was found -- surfaced by GET /v1/system/deps so
#: the response is diagnosable (e.g. "found it, but only via a WinGet
#: Packages glob, not on PATH") instead of a bare found/not-found bit.
KNOWN_LOCATION = "known_location"
ON_PATH = "path"
ENV_OVERRIDE = "env"


@dataclass(frozen=True)
class ResolvedBinary:
    path: str
    source: str  # ENV_OVERRIDE | ON_PATH | KNOWN_LOCATION


def _windows_locations(name: str) -> list[Path]:
    exe = f"{name}.exe"
    candidates: list[Path] = []

    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_root = Path(local_app_data) / "Microsoft" / "WinGet"
        # winget's own stable "Links" directory: a flat dir of shims/copies
        # winget maintains for every package it installs, regardless of
        # where the real payload lives -- checked before the deeper
        # Packages glob below because it's the more stable, intended-for-
        # this-purpose location.
        candidates.append(winget_root / "Links" / exe)

        # The real per-package install tree, e.g.
        # `...\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_.../
        # ffmpeg-7.1-full_build\bin\ffmpeg.exe` -- the exact subpath below
        # `Gyan.FFmpeg*` varies by ffmpeg build/version, hence the `**`
        # glob rather than a fixed path. Only meaningful for "ffmpeg" itself
        # (ffprobe ships in the same `bin` dir alongside it) -- yt-dlp isn't
        # distributed as a Gyan.FFmpeg package, so the glob simply matches
        # nothing for it, which is fine.
        packages_root = winget_root / "Packages"
        if packages_root.is_dir():
            # `sorted()` here is deliberately just deterministic lexicographic
            # ordering, not "newest version wins" -- glob()'s own order isn't
            # guaranteed stable across platforms/Python versions, and a
            # trailing "-full_build"/"-essentials_build"-style suffix in the
            # real directory name doesn't sort in a version-meaningful way
            # regardless. This only matters at all in the rare case of two
            # distinct Gyan.FFmpeg installs coexisting under one user's
            # WinGet Packages dir, which winget itself doesn't normally
            # produce (its own upgrade path replaces this dir in place); a
            # newest-mtime tiebreak was considered and skipped as
            # not-actually-trivial (mtime reflects the LAST FILE WRITE
            # inside the tree, not "the install winget currently considers
            # active" -- e.g. an unrelated later `chmod`/AV-scan touch would
            # silently change the winner) for a case this unlikely to occur.
            candidates.extend(sorted(packages_root.glob(f"Gyan.FFmpeg*/**/bin/{exe}")))

    program_files = os.environ.get("ProgramFiles")
    if program_files:
        candidates.append(Path(program_files) / "ffmpeg" / "bin" / exe)

    program_data = os.environ.get("ProgramData")
    if program_data:
        # Chocolatey shims every package's executables into one flat `bin`
        # dir, same idea as winget's Links dir above.
        candidates.append(Path(program_data) / "chocolatey" / "bin" / exe)

    return candidates


def _macos_locations(name: str) -> list[Path]:
    # Homebrew's two possible prefixes: Apple Silicon defaults to
    # /opt/homebrew, Intel Macs (and older installs) to /usr/local.
    return [Path("/opt/homebrew/bin") / name, Path("/usr/local/bin") / name]


def _linux_locations(name: str) -> list[Path]:
    return [
        Path("/usr/bin") / name,
        Path("/usr/local/bin") / name,
        Path.home() / ".local" / "bin" / name,
    ]


def _ensure_on_process_path(directory: Path) -> None:
    """Prepends `directory` to this process's own `PATH`, once.

    Idempotent by construction: checks membership (as a plain string
    comparison against the existing `os.pathsep`-split entries) before
    prepending, so repeated calls -- every `probe_media`/`normalize` call
    in every job re-resolves ffmpeg -- don't grow `PATH` without bound.
    Mutates the REAL `os.environ`, not a copy: that's the point (see the
    module docstring's demucs paragraph) -- any code in this same process
    that shells out to a bare binary name via its own PATH lookup, done
    at any point after this call, sees the same PATH this process does.
    """
    directory_str = str(directory)
    current = os.environ.get("PATH", "")
    entries = current.split(os.pathsep) if current else []
    if directory_str in entries:
        return
    os.environ["PATH"] = os.pathsep.join([directory_str, *entries]) if entries else directory_str


def _known_locations(name: str) -> list[Path]:
    system = platform.system()
    if system == "Windows":
        return _windows_locations(name)
    if system == "Darwin":
        return _macos_locations(name)
    # Treat anything else (Linux, other POSIX) the same way -- this app
    # only ships for Windows/macOS/Linux, so there is no fourth case to
    # distinguish today.
    return _linux_locations(name)


def resolve_binary(name: str) -> ResolvedBinary | None:
    """Resolves `name` (one of "ffmpeg", "ffprobe", "yt-dlp") to an absolute
    path, or `None` if it can't be found anywhere this module knows to look.

    Checked in order, first hit wins:
    1. The binary's env var override (`_ENV_OVERRIDES`), if set.
    2. `shutil.which(name)` -- plain PATH lookup.
    3. `_known_locations(name)` -- fixed, per-OS package-manager install
       locations, checked in the order returned (first existing FILE wins;
       a directory or broken symlink at that path is not a match). A hit
       here also prepends its directory to this process's own `PATH` (see
       `_ensure_on_process_path`) so third-party code in this same process
       that does its own bare-name PATH lookup (e.g. demucs) finds it too.
    """
    env_var = _ENV_OVERRIDES.get(name)
    if env_var:
        override = os.environ.get(env_var)
        if override:
            return ResolvedBinary(path=override, source=ENV_OVERRIDE)

    on_path = shutil.which(name)
    if on_path:
        return ResolvedBinary(path=on_path, source=ON_PATH)

    for candidate in _known_locations(name):
        if candidate.is_file():
            _ensure_on_process_path(candidate.parent)
            return ResolvedBinary(path=str(candidate), source=KNOWN_LOCATION)

    return None
