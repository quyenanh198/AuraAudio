"""Opt-in source-separation stage: isolates the target instrument from a
mixed recording (vocals/other instruments/percussion) before normalize/
inference. Detection-quality roadmap item 3 -- see
docs/superpowers/SESSION-HANDOFF.md's "Detection-quality roadmap" and
docs/benchmarks/2026-08-21-dq3.md.

Placed between probe and normalize (not after normalize): demucs's
separation quality depends on the STEREO, full-sample-rate signal --
normalize.py downmixes to mono at 22050Hz for the rest of the pipeline,
which would throw away the stereo cues source separation benefits from.
This stage therefore reads probe's raw downloaded source file directly and
writes a new (still full-quality) WAV that normalize.run then consumes
exactly as it would the original source -- every downstream stage
(normalize/inference/structure/quantize/assign/export) is unaware this
stage ever ran.

Only ever invoked for guitar projects with the project's
`settings.separateSource` flag set -- see aura_worker.runner's call site
and aura_worker.separation's module docstring for why piano is excluded
(the benchmark evidence does not support it).
"""
from __future__ import annotations

from pathlib import Path

from score_schema.models import JobErrorCode

from aura_worker.errors import JobFailure
from aura_worker.ffmpeg_utils import sha256_file
from aura_worker.separation import separate_guitar
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact

STAGE_VERSION = 1


def run(ctx: StageContext, source_path: Path) -> Path:
    out_path = ctx.workdir / "separated.wav"
    key = f"jobs/{ctx.job.id}/stage/separated.wav"

    cached = find_cached_artifact(ctx, "separate", STAGE_VERSION)
    if cached is not None:
        out_path.write_bytes(ctx.storage.get_bytes(cached.object_key))
        return out_path

    try:
        separate_guitar(source_path, out_path)
    except JobFailure:
        raise
    except Exception as exc:  # model/inference errors are not a stable type to catch narrowly
        raise JobFailure(JobErrorCode.MODEL_FAILED, f"source separation failed: {exc}") from exc

    ctx.storage.put_bytes(key, out_path.read_bytes())
    save_artifact(
        ctx, "separate", STAGE_VERSION, object_key=key,
        sha256=sha256_file(out_path), metrics={},
    )
    return out_path
