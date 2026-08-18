from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from aura_api.db import get_session
from aura_api.models import TranscriptionJob

logger = logging.getLogger(__name__)

_INTERRUPTED_DETAIL = (
    "job was interrupted — the application exited while it was running"
)


def recover_interrupted_jobs(session: Session | None = None) -> int:
    """Fail any job left at "running" by a previous launch.

    The job queue is an in-process thread pool holding no durable state, so
    a job that was running when the process died has no worker anywhere
    intending to finish it. Deliberately failed rather than re-queued:
    re-running inference costs minutes of CPU, and doing that unbidden on
    every launch is worse than showing a failed job the user can retry.

    Returns the number of jobs recovered.
    """
    owns_session = session is None
    session = session or get_session()
    try:
        stale = session.query(TranscriptionJob).filter(TranscriptionJob.status == "running").all()
        for job in stale:
            job.status = "failed"
            job.error_code = "INTERNAL_ERROR"
            job.error_detail = _INTERRUPTED_DETAIL
        if stale:
            session.commit()
            logger.warning("failed %d job(s) interrupted by a previous exit", len(stale))
        return len(stale)
    finally:
        if owns_session:
            session.close()
