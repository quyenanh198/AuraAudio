from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from score_schema.models import JobErrorCode

from aura_worker.binaries import resolve_binary, subprocess_flags
from aura_worker.errors import JobFailure

_ALLOWED_CODECS = {"pcm_s16le", "mp3", "aac", "h264"}


@dataclass
class ProbeInfo:
    container: str
    codec: str
    duration_ms: int
    sample_rate: int


def probe_media(path: Path) -> ProbeInfo:
    if not path.exists():
        raise JobFailure(JobErrorCode.DECODE_FAILED, f"file not found: {path}")

    ffprobe = resolve_binary("ffprobe")
    if ffprobe is None:
        raise JobFailure(
            JobErrorCode.DECODE_FAILED,
            "ffprobe not found -- install it (see the app's dependency banner) and try again",
        )

    try:
        proc = subprocess.run(
            [
                ffprobe.path, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=30, check=True,
            **subprocess_flags(),
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise JobFailure(JobErrorCode.DECODE_FAILED, f"ffprobe failed: {exc}") from exc

    data = json.loads(proc.stdout)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise JobFailure(JobErrorCode.UNSUPPORTED_MEDIA, "no audio stream found")

    stream = audio_streams[0]
    codec = stream.get("codec_name", "")
    if codec not in _ALLOWED_CODECS:
        raise JobFailure(JobErrorCode.UNSUPPORTED_MEDIA, f"unsupported codec: {codec}")

    fmt = data.get("format", {})
    duration_s = float(fmt.get("duration", stream.get("duration", 0)))
    container = Path(path).suffix.lstrip(".").lower() or fmt.get("format_name", "").split(",")[0]

    return ProbeInfo(
        container=container,
        codec=codec,
        duration_ms=int(duration_s * 1000),
        sample_rate=int(stream.get("sample_rate", 0)),
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
