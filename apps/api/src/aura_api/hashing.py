from __future__ import annotations

import hashlib


def compute_input_hash(media_sha256: str | None, object_key: str, instrument: str, pipeline_version: str) -> str:
    """Derive input_hash per ARCHITECTURE.md §6.

    media_sha256 is unknown until the worker's probe stage runs, so before that
    we hash the object_key instead — stable for the same upload, and object_key
    is already unique per upload.
    """
    basis = media_sha256 or object_key
    digest_input = f"{basis}:{instrument}:{pipeline_version}".encode()
    return hashlib.sha256(digest_input).hexdigest()
