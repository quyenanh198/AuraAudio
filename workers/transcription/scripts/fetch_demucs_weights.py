"""Build-time fetch for the source-separation model's pretrained weights.

Detection-quality roadmap item 3 (docs/superpowers/SESSION-HANDOFF.md
"Detection-quality roadmap"): an OPT-IN "isolate instrument from mix" step
before inference, using Meta's Demucs (`demucs` PyPI package, MIT-licensed
code). The weight file (~55MB) is the `htdemucs_6s` model -- see
docs/benchmarks/2026-08-21-dq3.md's "Candidate assessment" section for why
this specific model was chosen over the default 4-stem `htdemucs`. It is
NOT vendored into git, for the same reason as the piano checkpoint (see
fetch_piano_weights.py's docstring): no git-lfs in this repo, and a 55MB
binary would be a first-of-its-kind bloat to every clone.

demucs's own `get_model(name, repo=<local dir>)` loading path (see
`demucs.pretrained.get_model` / `demucs.repo.LocalRepo`) reads a small
`<name>.yaml` bag-of-models manifest plus one `<sig>-<checksum>.th` file per
model in the bag, from a local directory -- skipping its default
HuggingFace-hub download entirely whenever `repo` is not None. This script
is the checksum-pinned, offline-safe build-time equivalent: run it once at
build/dev-setup time, and aura_worker.separation always passes this
directory as `repo=`, so demucs's own network-download code path is never
reached at runtime.

Usage:
    uv run --package aura-worker python scripts/fetch_demucs_weights.py

Idempotent: skips the download if a file already sits at the destination
with the correct size and sha256.
"""
from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

# Meta's own legacy AWS/CloudFront model mirror -- the exact URL demucs
# itself would resolve to via demucs/remote/files.txt's
# "root: hybrid_transformer/" + "5c90dfd2-34c22ccb.th" line (the file lives
# under that root despite being listed under files.txt's own "Experimental
# 6 sources model" comment -- verified directly by resolving demucs's own
# `_parse_remote_files` parsing logic against a real install, not assumed
# from the comment structure) -- see docs/benchmarks/2026-08-21-dq3.md's
# "Candidate assessment" section.
WEIGHTS_URL = "https://dl.fbaipublicfiles.com/demucs/hybrid_transformer/5c90dfd2-34c22ccb.th"

# Verified directly against a real download during DQ-3's investigation:
# both size and sha256 computed from the actual downloaded file, then
# cross-checked against the truncated checksum baked into demucs's own
# filename convention (`5c90dfd2-34c22ccb.th` -- the `34c22ccb` suffix is
# itself a truncated sha256 prefix, per demucs.repo.LocalRepo.scan/
# check_checksum) before pinning the full sha256 here.
WEIGHTS_SHA256 = "34c22ccb381c6f9fdbf324f04e1e2fe21aaaf293f5ded163a162697ff9a02ddd"
WEIGHTS_SIZE_BYTES = 54_996_327

DEST_DIR = Path(__file__).resolve().parent.parent / "weights" / "demucs"
DEST_PATH = DEST_DIR / "5c90dfd2-34c22ccb.th"

# demucs.repo.BagOnlyRepo expects a `<name>.yaml` manifest next to the
# weight file(s) it names. This one is a fixed, tiny (20-byte), fully
# deterministic constant taken verbatim from demucs's own
# `demucs/remote/htdemucs_6s.yaml` -- not fetched over the network, since
# there is nothing to verify a checksum against for a file this trivial.
MANIFEST_NAME = "htdemucs_6s.yaml"
MANIFEST_CONTENTS = "models: ['5c90dfd2']\n"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _already_valid(path: Path) -> bool:
    if not path.exists():
        return False
    if path.stat().st_size != WEIGHTS_SIZE_BYTES:
        return False
    return _sha256(path) == WEIGHTS_SHA256


def fetch(dest_path: Path = DEST_PATH) -> Path:
    """Downloads the weights to `dest_path` if not already present and
    valid, and (re)writes the manifest yaml next to it. Returns the final
    weights path. Raises RuntimeError on a checksum mismatch."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    (dest_path.parent / MANIFEST_NAME).write_text(MANIFEST_CONTENTS)

    if _already_valid(dest_path):
        print(f"demucs weights already present and verified: {dest_path}")
        return dest_path

    tmp_path = dest_path.with_suffix(".th.partial")
    size_mb = WEIGHTS_SIZE_BYTES // 1_000_000
    print(f"downloading demucs weights (~{size_mb}MB) from {WEIGHTS_URL}")
    # Build-time only (never at transcription-request time); checksum-verified below.
    urllib.request.urlretrieve(WEIGHTS_URL, tmp_path)  # noqa: S310

    actual_size = tmp_path.stat().st_size
    actual_sha256 = _sha256(tmp_path)
    if actual_size != WEIGHTS_SIZE_BYTES or actual_sha256 != WEIGHTS_SHA256:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            "demucs weights download failed checksum verification: "
            f"size={actual_size} (expected {WEIGHTS_SIZE_BYTES}), "
            f"sha256={actual_sha256} (expected {WEIGHTS_SHA256})"
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
