from __future__ import annotations

import shutil
import subprocess

from fastapi import APIRouter

from aura_api.schemas import DependencyStatus, SystemDepsResponse

router = APIRouter(tags=["system"])

_VERSION_CHECK_TIMEOUT_SECONDS = 5


def _parse_version(binary: str, executable_path: str) -> str | None:
    """Best-effort extraction of a version token from `<binary> -version`.

    Never raises: a subprocess failure, timeout, or unrecognized output shape
    all fall through to `None` — the binary is still reported as `found`,
    since `shutil.which` already proved it's on PATH.
    """
    try:
        proc = subprocess.run(
            [executable_path, "-version"],
            capture_output=True,
            text=True,
            timeout=_VERSION_CHECK_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return None

    first_line = proc.stdout.splitlines()[0] if proc.stdout else ""
    tokens = first_line.split()
    # Expected shape: "ffmpeg version 6.1.1-3ubuntu5 Copyright ..."
    if len(tokens) >= 3 and tokens[0] == binary and tokens[1] == "version":
        return tokens[2]
    return None


def _check_binary(binary: str) -> DependencyStatus:
    executable_path = shutil.which(binary)
    if executable_path is None:
        return DependencyStatus(found=False, version=None)
    return DependencyStatus(found=True, version=_parse_version(binary, executable_path))


@router.get("/system/deps", response_model=SystemDepsResponse)
def get_system_deps() -> SystemDepsResponse:
    ffmpeg = _check_binary("ffmpeg")
    ffprobe = _check_binary("ffprobe")
    return SystemDepsResponse(
        ffmpeg=ffmpeg,
        ffprobe=ffprobe,
        allFound=ffmpeg.found and ffprobe.found,
    )
