from __future__ import annotations

import logging
import re
import subprocess

from aura_worker.binaries import ResolvedBinary, resolve_binary, subprocess_flags
from fastapi import APIRouter

from aura_api.schemas import DependencyStatus, SystemDepsResponse

_logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

_VERSION_CHECK_TIMEOUT_SECONDS = 5

# yt-dlp's real `--version` output is a bare date-shaped string (e.g.
# "2024.08.06", occasionally with a trailing release suffix), not the
# "<binary> version X" shape ffmpeg/ffprobe use — so it gets its own
# lenient-but-not-garbage pattern rather than reusing `_parse_version`.
_YT_DLP_VERSION_PATTERN = re.compile(r"^\d{4}\.\d{2}\.\d{2}(\S*)?$")


def _parse_version(binary: str, executable_path: str) -> str | None:
    """Best-effort extraction of a version token from `<binary> -version`.

    Never raises: a subprocess failure, timeout, or unrecognized output shape
    all fall through to `None` — the binary is still reported as `found`,
    since `resolve_binary` already proved it exists (either on PATH or at
    one of its known per-OS install locations — see
    `aura_worker.binaries`).
    """
    try:
        proc = subprocess.run(
            [executable_path, "-version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_CHECK_TIMEOUT_SECONDS,
            check=False,
            **subprocess_flags(),
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None

    first_line = proc.stdout.splitlines()[0] if proc.stdout else ""
    tokens = first_line.split()
    # Expected shape: "ffmpeg version 6.1.1-3ubuntu5 Copyright ..."
    if len(tokens) >= 3 and tokens[0] == binary and tokens[1] == "version":
        return tokens[2]
    return None


def _resolve_binary_safe(binary: str) -> ResolvedBinary | None:
    """`resolve_binary`, but never lets an unexpected exception escape.

    `resolve_binary` is itself documented to never raise (see its own
    docstring), but this endpoint exists SPECIFICALLY to report dependency
    health -- it must stay a working diagnostic even if that contract is
    ever violated by a future change (here, or in a dependency this router
    doesn't control). Degrading to "not found" here is strictly better than
    a bare 500: a wrong-but-diagnosable `found: false` still lets the
    frontend show install guidance, instead of the whole panel erroring out.
    """
    try:
        return resolve_binary(binary)
    except Exception:
        _logger.warning("resolve_binary(%r) raised unexpectedly; reporting not found", binary, exc_info=True)
        return None


def _check_binary(binary: str) -> DependencyStatus:
    # Resolved (not a bare `shutil.which`), so this reports "found" for the
    # exact same set of binaries the real probe/normalize/import call sites
    # can actually use -- see aura_worker.binaries's module docstring for
    # why plain PATH lookup alone isn't enough on Windows.
    resolved = _resolve_binary_safe(binary)
    if resolved is None:
        return DependencyStatus(found=False, version=None, path=None, source=None)
    return DependencyStatus(
        found=True,
        version=_parse_version(binary, resolved.path),
        path=resolved.path,
        source=resolved.source,
    )


def _parse_yt_dlp_version(executable_path: str) -> str | None:
    """Best-effort extraction of yt-dlp's version from `yt-dlp --version`.

    Never raises, same contract as `_parse_version`: any subprocess
    failure, timeout, or unrecognized output shape falls through to
    `None` — the binary is still reported as `found` since `resolve_binary`
    already proved it exists (PATH or a known install location).
    """
    try:
        proc = subprocess.run(
            [executable_path, "--version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_CHECK_TIMEOUT_SECONDS,
            check=False,
            **subprocess_flags(),
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None

    first_line = proc.stdout.strip().splitlines()[0] if proc.stdout.strip() else ""
    if _YT_DLP_VERSION_PATTERN.match(first_line):
        return first_line
    return None


def _check_yt_dlp() -> DependencyStatus:
    resolved = _resolve_binary_safe("yt-dlp")
    if resolved is None:
        return DependencyStatus(found=False, version=None, path=None, source=None)
    return DependencyStatus(
        found=True,
        version=_parse_yt_dlp_version(resolved.path),
        path=resolved.path,
        source=resolved.source,
    )


@router.get("/system/deps", response_model=SystemDepsResponse)
def get_system_deps() -> SystemDepsResponse:
    ffmpeg = _check_binary("ffmpeg")
    ffprobe = _check_binary("ffprobe")
    yt_dlp = _check_yt_dlp()
    return SystemDepsResponse(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        ytDlp=yt_dlp,
        # CRITICAL: yt-dlp is an OPTIONAL, guided-install dependency (see
        # module docstring intent in schemas.py) — it must never affect
        # `allFound`, which stays scoped to the REQUIRED deps (ffmpeg +
        # ffprobe) that block transcription. The frontend's blocking banner
        # keys off `allFound`, so widening this would incorrectly block
        # transcription for users who never intend to use YouTube import.
        allFound=ffmpeg.found and ffprobe.found,
    )
