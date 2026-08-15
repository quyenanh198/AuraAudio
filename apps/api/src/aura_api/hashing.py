from __future__ import annotations

import hashlib


def compute_input_hash(object_key: str, instrument: str, pipeline_version: str) -> str:
    """Derive input_hash per ARCHITECTURE.md §6.

    Keyed on object_key rather than the media's sha256: the API must never
    download media to hash it (§4.2, §7 — no proxying large media through the
    application process), and sha256 is only known once the worker's probe
    stage has run. object_key is assigned once at upload time and never
    changes, so it stays stable across repeated calls for the same upload —
    unlike sha256, which would silently change the hash basis (and break
    idempotency) the moment probe finishes.
    """
    digest_input = f"{object_key}:{instrument}:{pipeline_version}".encode()
    return hashlib.sha256(digest_input).hexdigest()
