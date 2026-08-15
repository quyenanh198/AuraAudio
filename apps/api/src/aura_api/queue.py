from __future__ import annotations

from redis import Redis
from rq import Queue

from aura_api.config import settings

_redis = Redis.from_url(settings.redis_url)
transcription_queue = Queue("transcription", connection=_redis)


def enqueue_transcription_job(job_id: str) -> None:
    transcription_queue.enqueue("aura_worker.runner.run_transcription_job", job_id, job_id=job_id)
