from __future__ import annotations

from aura_api.models import MediaAsset
from aura_worker.errors import JobFailure
from aura_worker.ffmpeg_utils import ProbeInfo, probe_media, sha256_file
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import JobErrorCode

MAX_DURATION_MS = 15 * 60 * 1000
MAX_BYTES = 500 * 1024 * 1024
STAGE_VERSION = 1


def run(ctx: StageContext) -> ProbeInfo:
    cached = find_cached_artifact(ctx, "probe", STAGE_VERSION)
    if cached is not None:
        return ProbeInfo(
            container=cached.metrics["container"],
            codec=cached.metrics["codec"],
            duration_ms=cached.metrics["duration_ms"],
            sample_rate=cached.metrics["sample_rate"],
        )

    asset = ctx.session.get(MediaAsset, ctx.job.media_asset_id)
    local_path = ctx.workdir / "source" / "input"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage.download_media_asset(asset.object_key, local_path)

    info = probe_media(local_path)
    if info.duration_ms > MAX_DURATION_MS:
        raise JobFailure(JobErrorCode.MEDIA_TOO_LARGE, f"duration {info.duration_ms}ms exceeds limit")

    digest = sha256_file(local_path)
    asset.sha256 = digest
    asset.duration_ms = info.duration_ms
    ctx.session.commit()

    save_artifact(
        ctx, "probe", STAGE_VERSION,
        object_key=f"jobs/{ctx.job.id}/stage/probe.json", sha256=digest,
        metrics={
            "container": info.container, "codec": info.codec,
            "duration_ms": info.duration_ms, "sample_rate": info.sample_rate,
        },
    )
    return info
