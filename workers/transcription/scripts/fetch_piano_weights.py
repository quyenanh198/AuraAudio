"""Build-time fetch for the piano transcription model's pretrained checkpoint.

Detection-quality roadmap item 2 (docs/superpowers/SESSION-HANDOFF.md
"Detection-quality roadmap"). The checkpoint (~164MB) is the trained model
for ByteDance/Kong's "High-resolution Piano Transcription with Pedals by
Regressing Onsets and Offsets Times" (Zenodo record 4034264, CC-BY-4.0 --
see docs/benchmarks/2026-08-21-dq2.md's license record). It is NOT vendored
into git: this repo has no git-lfs configured (verified: `git lfs env` ->
"'lfs' is not a git command"), and a single 164MB binary committed directly
would be a first-of-its-kind, permanent bloat to every future clone -- the
existing largest tracked binaries are the ~350-430KB tonejs soundfont
samples under apps/desktop/web/src/assets/soundfonts/. Instead this mirrors
how basic-pitch's own weights are handled: basic-pitch ships its weights as
PyPI *package data* (collected into the PyInstaller bundle via
`--collect-data basic_pitch` in apps/desktop/build-backend.sh, never
touching git). piano_transcription_inference has no such package-data
mechanism -- its own upstream code just wgets the checkpoint into
~/.piano_transcription_inference_data/ on first use if no local
checkpoint_path is given. This script is the checksum-pinned, offline-safe
equivalent: run it once at build/dev-setup time (never at transcription
request time), and aura_worker.piano_engine always passes its output path
as an explicit checkpoint_path, so piano_transcription_inference's own
download-on-demand code path is never reached at runtime.

Usage:
    uv run --package aura-worker python scripts/fetch_piano_weights.py

Idempotent: skips the download if a file already sits at the destination
with the correct size and sha256 (mirrors piano_transcription_inference's
own size-based skip-check, but adds a real hash comparison on top of it --
see CHECKPOINT_SHA256's derivation note below).
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

# Zenodo record 4034264 (https://zenodo.org/records/4034264), the official
# checkpoint distribution point named directly in
# piano_transcription_inference's own inference.py. Zenodo assigns a
# permanent DOI-backed URL per file; this record has exactly one file.
CHECKPOINT_URL = (
    "https://zenodo.org/records/4034264/files/"
    "CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1"
)

# Verified directly against a real download during DQ-2's investigation --
# both the byte size AND sha256 below were computed from the actual
# downloaded file, then cross-checked against Zenodo's own published md5
# (22b961b77c1878239fec963362097045, from the record's `/api/records/...`
# metadata) to confirm the download wasn't corrupted or a stale mirror
# before pinning the sha256 here. Not copied from any third party.
CHECKPOINT_SHA256 = "c3fa9730725bf4a762f1c14bc80cd5986eacda01b026f5a4a2525cd607876141"
CHECKPOINT_SIZE_BYTES = 171_966_578

DEST_DIR = Path(__file__).resolve().parent.parent / "weights" / "piano"
DEST_PATH = DEST_DIR / "piano_transcription_crnn.pth"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _already_valid(path: Path) -> bool:
    if not path.exists():
        return False
    if path.stat().st_size != CHECKPOINT_SIZE_BYTES:
        return False
    return _sha256(path) == CHECKPOINT_SHA256


def fetch(dest_path: Path = DEST_PATH) -> Path:
    """Downloads the checkpoint to `dest_path` if not already present and
    valid. Returns the final path. Raises RuntimeError on a checksum
    mismatch (a corrupted download or an unexpected upstream file change --
    fail loudly rather than silently loading a wrong/tampered model)."""
    if _already_valid(dest_path):
        print(f"piano checkpoint already present and verified: {dest_path}")
        return dest_path

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(".pth.partial")
    size_mb = CHECKPOINT_SIZE_BYTES // 1_000_000
    print(f"downloading piano checkpoint (~{size_mb}MB) from {CHECKPOINT_URL}")
    # Build-time only (never at transcription-request time); checksum-verified below.
    urllib.request.urlretrieve(CHECKPOINT_URL, tmp_path)  # noqa: S310

    actual_size = tmp_path.stat().st_size
    actual_sha256 = _sha256(tmp_path)
    if actual_size != CHECKPOINT_SIZE_BYTES or actual_sha256 != CHECKPOINT_SHA256:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            "piano checkpoint download failed checksum verification: "
            f"size={actual_size} (expected {CHECKPOINT_SIZE_BYTES}), "
            f"sha256={actual_sha256} (expected {CHECKPOINT_SHA256})"
        )

    tmp_path.rename(dest_path)
    print(f"verified and saved: {dest_path}")
    return dest_path


if __name__ == "__main__":
    try:
        fetch()
    except Exception as exc:  # build-time script: any failure should be loud and non-zero-exit
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
