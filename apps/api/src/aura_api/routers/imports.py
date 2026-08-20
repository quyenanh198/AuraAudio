from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException

from aura_api.config import settings
from aura_api.schemas import ImportYoutubeRequest, ImportYoutubeResponse, make_upload_object_key
from aura_api.storage import storage_client

router = APIRouter(tags=["imports"])

# YouTube import is the app's FIRST network-using feature (see
# docs/superpowers/SESSION-HANDOFF.md). yt-dlp is an OPTIONAL PATH
# dependency, guided-install like ffmpeg (routers/system.py's `ytDlp`
# entry), but never blocking: it is not bundled and never added to the
# desktop app's deb `Depends` (apps/desktop/src-tauri/tauri.conf.json).
_ALLOWED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}

_YT_DLP_TIMEOUT_SECONDS = 300
_MAX_FILESIZE = "200m"
_STDERR_TAIL_CHARS = 300
# Prefix marker for the `--print` line yt-dlp emits for the video title, so
# it can be picked out of stdout without a second (non-"cheap") metadata
# request — normal progress/status lines never start with this literal.
_TITLE_MARKER = "AURA_YT_TITLE:"


def _validate_youtube_url(url: str) -> str:
    """Returns `url` unchanged if it's an http(s) YouTube URL, else raises
    HTTPException(422). Hostname is checked on the PARSED result (after
    userinfo/port stripping), never on a substring of the raw string --
    `https://youtube.com@evil.com/...` must be rejected, not accepted,
    because its real (parsed) hostname is `evil.com`.
    """
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"malformed URL: {exc}") from exc

    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=422,
            detail=f"unsupported URL scheme {parsed.scheme!r}: only http/https are accepted",
        )

    try:
        hostname = parsed.hostname
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"malformed URL host: {exc}") from exc

    if hostname is None or hostname.lower() not in _ALLOWED_HOSTS:
        raise HTTPException(
            status_code=422,
            detail=f"unsupported host {hostname!r}: only YouTube URLs are accepted",
        )
    return url


def _extract_title(stdout: str) -> str | None:
    for line in stdout.splitlines():
        if line.startswith(_TITLE_MARKER):
            title = line[len(_TITLE_MARKER) :].strip()
            return title or None
    return None


def _stderr_tail(text: str | None) -> str:
    if not text:
        return ""
    return text[-_STDERR_TAIL_CHARS:]


@router.post("/imports/youtube", response_model=ImportYoutubeResponse, status_code=201)
def import_youtube(body: ImportYoutubeRequest) -> ImportYoutubeResponse:
    url = _validate_youtube_url(body.url)

    yt_dlp_path = shutil.which("yt-dlp")
    if yt_dlp_path is None:
        # Machine-readable so the frontend can show install guidance
        # (mirrors the ffmpeg-missing banner's install-command pattern)
        # instead of a raw error string.
        raise HTTPException(
            status_code=409,
            detail={
                "code": "yt_dlp_not_found",
                "message": "yt-dlp was not found on PATH. Install it to import audio from YouTube.",
            },
        )

    imports_root = Path(settings.data_dir) / "imports_tmp"
    imports_root.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=imports_root))

    try:
        # mp3 (not the source webm/opus) because the transcription worker's
        # probe step only accepts {"pcm_s16le", "mp3", "aac", "h264"}
        # (workers/transcription/src/aura_worker/ffmpeg_utils.py).
        cmd = [
            yt_dlp_path,
            "--no-playlist",
            "--max-filesize",
            _MAX_FILESIZE,
            "-x",
            "--audio-format",
            "mp3",
            "--print",
            f"{_TITLE_MARKER}%(title)s",
            "-o",
            f"{tmp_dir}/%(id)s.%(ext)s",
            # `--` marks the end of options: defense-in-depth so a future
            # loosening of `_validate_youtube_url` can't turn a
            # dash-prefixed "url" into an extra yt-dlp flag. Unexploitable
            # today (the hostname check already rejects anything that
            # wouldn't parse as a real http(s) YouTube URL), but cheap.
            "--",
            url,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_YT_DLP_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(
                status_code=502,
                detail=f"yt-dlp timed out after {_YT_DLP_TIMEOUT_SECONDS}s: {_stderr_tail(exc.stderr)}",
            ) from exc
        except (OSError, ValueError) as exc:
            # `urlsplit`/`.hostname` accept strings that later fail at the
            # OS-exec boundary -- e.g. an embedded NUL byte in the path or
            # query survives hostname validation (the hostname component
            # itself is clean) but `subprocess.run` raises `ValueError:
            # embedded null byte` when handed it as an argv element.
            # Treated as a client-input problem (422), not a server error
            # (502) or an internal-details leak (exc's message is
            # deliberately not included).
            raise HTTPException(status_code=422, detail="invalid URL") from exc

        if proc.returncode != 0:
            raise HTTPException(status_code=502, detail=f"yt-dlp failed: {_stderr_tail(proc.stderr)}")

        mp3_files = sorted(tmp_dir.glob("*.mp3"))
        if not mp3_files:
            raise HTTPException(
                status_code=502,
                detail="yt-dlp reported success but produced no audio file",
            )

        downloaded = mp3_files[0]
        object_key = make_upload_object_key(downloaded.name)
        storage_client.put_bytes(object_key, downloaded.read_bytes())

        return ImportYoutubeResponse(object_key=object_key, title=_extract_title(proc.stdout))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
