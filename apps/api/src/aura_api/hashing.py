from __future__ import annotations

import hashlib


def compute_input_hash(
    object_key: str, instrument: str, pipeline_version: str, separate_source: bool = False
) -> str:
    """Derive input_hash per ARCHITECTURE.md §6.

    Keyed on object_key rather than the media's sha256: the API must never
    download media to hash it (§4.2, §7 — no proxying large media through the
    application process), and sha256 is only known once the worker's probe
    stage has run. object_key is assigned once at upload time and never
    changes, so it stays stable across repeated calls for the same upload —
    unlike sha256, which would silently change the hash basis (and break
    idempotency) the moment probe finishes.

    `separate_source` (detection-quality roadmap item 3's opt-in
    "isolate instrument from mix" project setting) is folded into the hash
    so toggling it produces a distinct input_hash -- a new
    TranscriptionJob row, not a reuse of a job/artifact cache keyed under
    the flag's other state. Without this, POST .../transcriptions would
    return the *existing* job for the same object_key/instrument/
    pipeline_version after a user flips the project's separation setting
    and re-requests a transcription, silently ignoring the toggle.
    """
    digest_input = f"{object_key}:{instrument}:{pipeline_version}:{separate_source}".encode()
    return hashlib.sha256(digest_input).hexdigest()
