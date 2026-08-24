from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from aura_worker.binaries import ResolvedBinary, resolve_binary, subprocess_flags
from fastapi import APIRouter, HTTPException

from aura_api.config import settings
from aura_api.schemas import ImportYoutubeRequest, ImportYoutubeResponse, make_upload_object_key
from aura_api.storage import storage_client

router = APIRouter(tags=["imports"])
_logger = logging.getLogger(__name__)

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
_MAX_FILESIZE = "200m"  # yt-dlp's own --max-filesize syntax (lowercase "m" = MiB)
_MAX_FILESIZE_HUMAN = "200MB"  # for user-facing messages -- keep in sync with _MAX_FILESIZE above
_STDERR_TAIL_CHARS = 300
# Prefix marker for the `--print` line yt-dlp emits for the video title, so
# it can be picked out of stdout without a second (non-"cheap") metadata
# request — normal progress/status lines never start with this literal.
#
# TRAP (verified against yt-dlp's own source/docs, real-Windows regression):
# `--print TEMPLATE` on its own IMPLIES `--simulate` (i.e. "skip download")
# unless `--no-simulate` is *also* passed. Without `--no-simulate` in `cmd`
# below, yt-dlp happily prints this title line, exits 0, and downloads
# NOTHING -- which then fell through to the generic "produced no audio
# file" 502 with zero signal that print-only mode, not a real failure, was
# the cause. `--no-simulate` MUST stay in `cmd` alongside `--print` or this
# regresses silently (the old stub-based tests didn't model this yt-dlp
# semantic at all, which is exactly how it shipped broken).
_TITLE_MARKER = "AURA_YT_TITLE:"

# yt-dlp's real message (verified against yt-dlp's own source) when
# `--max-filesize` rejects a video: it SKIPS the download entirely and
# still exits 0 -- there is no file to find afterward, and (without this
# check) that skip fell through to the same generic "produced no audio
# file" 502 as a genuine bug, giving the user zero signal that the fix was
# simply "pick a smaller/lower-quality video". Matched case-insensitively
# against combined stdout+stderr, independent of returncode, since this is
# a content-based signal, not an exit-code one.
_MAX_FILESIZE_SKIP_MARKER = "file is larger than max-filesize"

# `-x --audio-format mp3` normally guarantees an mp3 output, but yt-dlp can
# still finish (exit 0) without one -- e.g. the mp3 postprocess step
# silently no-ops when ffmpeg isn't on yt-dlp's own PATH, leaving the
# originally-downloaded audio container behind instead. mp3 is checked
# first (the expected/common case); the rest are fallback containers
# actually seen from yt-dlp's audio-only format selection. Whichever file
# is found is uploaded as-is -- no transcoding is performed here (the
# worker's probe step validates the actual codec, not the extension, and
# rejects anything it can't handle with its own clear error).
_FALLBACK_AUDIO_EXTENSIONS = ("mp3", "m4a", "opus", "webm", "wav")


def _resolve_binary_or_502(name: str) -> ResolvedBinary | None:
    """`resolve_binary`, but converts an unexpected exception into a
    diagnosable 502 instead of letting it fall through as a bare 500.

    `resolve_binary` documents itself as never-raising (see
    `aura_worker.binaries`), but this is the SAME resolver whose new
    known-location search already caused a real-Windows regression here
    once (a filesystem probe raising where the old, dumber
    `shutil.which`-only version structurally couldn't) -- this endpoint
    stays a working, non-blocking import flow even if that contract is
    ever violated again by a future change, instead of the whole request
    dying with no machine-readable detail for the frontend to show.
    """
    try:
        return resolve_binary(name)
    except Exception as exc:
        _logger.warning("resolve_binary(%r) raised unexpectedly", name, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail={
                "code": "binary_resolution_failed",
                "message": f"Could not determine whether {name} is installed on this machine.",
            },
        ) from exc


def _is_max_filesize_skip(combined_output: str) -> bool:
    return _MAX_FILESIZE_SKIP_MARKER in combined_output.lower()


def _find_downloaded_audio(tmp_dir: Path) -> Path | None:
    for ext in _FALLBACK_AUDIO_EXTENSIONS:
        matches = sorted(tmp_dir.glob(f"*.{ext}"))
        if matches:
            return matches[0]
    return None


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

    # Resolved (not a bare `shutil.which`) for the same reason
    # ffmpeg_utils.probe_media/normalize are: on Windows in particular, a
    # freshly-`winget install`ed binary can be invisible to this already-
    # running process's PATH until the app restarts (see
    # aura_worker.binaries's module docstring) even though it's really
    # installed. Checking winget's own stable install locations too closes
    # that gap without requiring a restart.
    yt_dlp = _resolve_binary_or_502("yt-dlp")
    if yt_dlp is None:
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
    yt_dlp_path = yt_dlp.path

    # yt-dlp's OWN internal ffmpeg lookup is independent of ours -- it does
    # its own PATH search when postprocessing (`-x --audio-format mp3`),
    # which can miss the exact same known-but-not-on-PATH ffmpeg install
    # this module just resolved for itself. `--ffmpeg-location <dir>`
    # (yt-dlp's own documented flag, accepts either the binary's directory
    # or the binary's exact path) points it at whatever we found, so its
    # own extraction step doesn't independently fail the same way. Omitted
    # entirely (not passed as an empty string) when ffmpeg can't be
    # resolved at all -- yt-dlp then falls through to its own PATH search
    # and, ultimately, its own natural "ffmpeg not found" failure.
    ffmpeg = _resolve_binary_or_502("ffmpeg")
    ffmpeg_location_args = (
        ["--ffmpeg-location", str(Path(ffmpeg.path).parent)] if ffmpeg is not None else []
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
            *ffmpeg_location_args,
            "--print",
            f"{_TITLE_MARKER}%(title)s",
            # See the `_TITLE_MARKER` comment above: `--print` alone implies
            # `--simulate` (no download). This flag is what makes the
            # actual download happen.
            "--no-simulate",
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
                **subprocess_flags(),
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

        # Checked before the returncode branch below and independent of it:
        # the max-filesize skip is a content-based signal (yt-dlp's own
        # message), not an exit-code one -- real yt-dlp exits 0 for this
        # case, but a future/different version misbehaving with a nonzero
        # code here should still get the specific, actionable 422 rather
        # than the generic 502.
        combined_output = f"{proc.stdout}\n{proc.stderr}"
        if _is_max_filesize_skip(combined_output):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"video is larger than the {_MAX_FILESIZE_HUMAN} import size limit; "
                    "yt-dlp skipped the download"
                ),
            )

        if proc.returncode != 0:
            raise HTTPException(status_code=502, detail=f"yt-dlp failed: {_stderr_tail(proc.stderr)}")

        downloaded = _find_downloaded_audio(tmp_dir)
        if downloaded is None:
            raise HTTPException(
                status_code=502,
                detail=(
                    "yt-dlp reported success but produced no audio file. "
                    f"yt-dlp output: {_stderr_tail(combined_output)}"
                ),
            )

        object_key = make_upload_object_key(downloaded.name)
        storage_client.put_bytes(object_key, downloaded.read_bytes())

        return ImportYoutubeResponse(object_key=object_key, title=_extract_title(proc.stdout))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
