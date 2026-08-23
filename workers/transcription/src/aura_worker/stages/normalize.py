from __future__ import annotations

import subprocess
from pathlib import Path

from score_schema.models import JobErrorCode

from aura_worker.binaries import resolve_binary
from aura_worker.errors import JobFailure
from aura_worker.ffmpeg_utils import sha256_file
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact

TARGET_SAMPLE_RATE = 22050
STAGE_VERSION = 1


def run(ctx: StageContext, source_path: Path) -> Path:
    out_path = ctx.workdir / "normalized.wav"
    key = f"jobs/{ctx.job.id}/stage/normalized.wav"

    cached = find_cached_artifact(ctx, "normalize", STAGE_VERSION)
    if cached is not None:
        out_path.write_bytes(ctx.storage.get_bytes(cached.object_key))
        return out_path

    ffmpeg = resolve_binary("ffmpeg")
    if ffmpeg is None:
        raise JobFailure(
            JobErrorCode.DECODE_FAILED,
            "ffmpeg not found -- install it (see the app's dependency banner) and try again",
        )

    try:
        subprocess.run(
            [
                ffmpeg.path, "-y", "-i", str(source_path),
                "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE),
                "-af", "loudnorm=I=-23:TP=-2:LRA=7",
                str(out_path),
            ],
            capture_output=True, timeout=120, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise JobFailure(JobErrorCode.DECODE_FAILED, f"ffmpeg normalize failed: {exc.stderr!r}") from exc

    ctx.storage.put_bytes(key, out_path.read_bytes())
    save_artifact(
        ctx, "normalize", STAGE_VERSION, object_key=key,
        sha256=sha256_file(out_path), metrics={"sample_rate": TARGET_SAMPLE_RATE},
    )
    return out_path
