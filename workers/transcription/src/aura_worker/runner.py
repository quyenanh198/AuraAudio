from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from aura_api.db import get_engine
from aura_api.models import TranscriptionJob
from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import export as export_stage
from aura_worker.stages import assign, inference, normalize, probe, quantize, structure
from aura_worker.storage import WorkerStorageClient

logger = logging.getLogger(__name__)

_SessionLocal = sessionmaker(bind=get_engine())


def run_transcription_job(job_id: str) -> None:
    session: Session = _SessionLocal()
    storage = WorkerStorageClient()
    try:
        job = session.get(TranscriptionJob, job_id)
        if job is None:
            logger.error("job %s not found", job_id)
            return
        if job.status == "succeeded":
            logger.info("job %s already succeeded, skipping re-run", job_id)
            return

        job.status = "running"
        session.commit()

        with tempfile.TemporaryDirectory() as tmp:
            ctx = StageContext(job=job, session=session, storage=storage, workdir=Path(tmp))

            probe.run(ctx)
            # probe.run downloads the source to ctx.workdir/"source"/"input" (a fixed
            # convention, not a return value) so normalize.run can find it on resume.
            normalized_path = normalize.run(ctx, source_path=ctx.workdir / "source" / "input")
            notes = inference.run(ctx, normalized_path=normalized_path)
            structure_result = structure.run(ctx, normalized_path=normalized_path, notes=notes)
            score = quantize.run(ctx, notes, structure_result)
            score = assign.run(ctx, score)
            export_stage.run(ctx, notes=notes, score=score)

    except JobFailure as exc:
        session.rollback()
        job = session.get(TranscriptionJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error_code = exc.code.value
            job.error_detail = exc.detail
            session.commit()
    except Exception as exc:  # unexpected — still record for the API to surface
        session.rollback()
        job = session.get(TranscriptionJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error_code = "INTERNAL_ERROR"
            job.error_detail = str(exc)
            session.commit()
        raise
    finally:
        session.close()
