from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from aura_worker.rederive import run_rederive_job
from aura_worker.runner import run_transcription_job

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)


def enqueue_transcription_job(job_id: str) -> None:
    future = _executor.submit(run_transcription_job, job_id)
    future.add_done_callback(_log_unexpected_failure)


def enqueue_rederive_job(job_id: str) -> None:
    future = _executor.submit(run_rederive_job, job_id)
    future.add_done_callback(_log_unexpected_failure)


def _log_unexpected_failure(future) -> None:
    exc = future.exception()
    if exc is not None:
        logger.error("transcription job raised an unhandled exception", exc_info=exc)
