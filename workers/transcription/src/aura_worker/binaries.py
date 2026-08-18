from __future__ import annotations

import os


def _resolve(env_var: str, default: str) -> str:
    """An explicit path if one is configured, else the bare name on PATH.

    A packaged desktop app ships its own ffmpeg/ffprobe and points these at
    them, because a user's machine cannot be assumed to have either. The
    bare-name fallback is what keeps the developer workflow and the test
    suite working with no configuration at all.

    A set-but-blank value falls back rather than producing an empty argv[0],
    which would fail with a confusing error far from its cause.
    """
    value = os.environ.get(env_var, "").strip()
    return value or default


def ffmpeg_path() -> str:
    return _resolve("AURA_FFMPEG_PATH", "ffmpeg")


def ffprobe_path() -> str:
    return _resolve("AURA_FFPROBE_PATH", "ffprobe")
