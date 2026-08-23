from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from aura_api.db import get_engine
from aura_api.models import Export, Project, ScoreRevision, TranscriptionJob
from aura_api.storage import LocalStorageClient
from aura_worker.fingering import StringFret, assign_measure as assign_string_fret
from aura_worker.piano_hands import assign_measure as assign_hands
from musicxml.export import score_json_to_musicxml
from score_schema.validate import validate_score

logger = logging.getLogger(__name__)

_SessionLocal = sessionmaker(bind=get_engine())

# Score events carry no velocity field (see score_schema.validate's
# _EVENT_SCHEMA) — mido note_on messages need one, so re-derived exports use
# a fixed velocity, matching the constant the export stage's own tests
# exercise (NoteEvent(..., velocity=90, ...) in test_export.py).
_DEFAULT_VELOCITY = 90


def _reassign_with_locks(score: dict) -> dict:
    part = score["parts"][0]
    instrument = part["instrument"]
    for measure in part["measures"]:
        events = measure["events"]
        if instrument == "guitar":
            locked = {
                i: StringFret(string=e["string"], fret=e["fret"])
                for i, e in enumerate(events)
                if e["locked"] and e["string"] is not None and e["fret"] is not None
            }
            assignments = assign_string_fret(events, locked=locked)
            for i, event in enumerate(events):
                sf = assignments.get(i)
                event["string"] = sf.string if sf is not None else None
                event["fret"] = sf.fret if sf is not None else None
                event["hand"] = None
        else:
            locked = {
                i: e["hand"] for i, e in enumerate(events)
                if e["locked"] and e["hand"] in ("left", "right")
            }
            assignments = assign_hands(events, locked=locked)
            for i, event in enumerate(events):
                event["hand"] = assignments.get(i)
                event["string"] = None
                event["fret"] = None
    validate_score(score)
    return score


def _write_midi_from_score(score: dict, out_path: Path) -> None:
    """Mirror stages/export.py:_write_midi's mido usage exactly, but source
    events (pitch, onsetSeconds, offsetSeconds) from the score's own measures
    instead of raw inference NoteEvents, sorted by onset, with tempo read
    from score["parts"][0]["tempoBpm"]."""
    import mido

    part = score["parts"][0]
    tempo_bpm = part["tempoBpm"]
    events = [
        event
        for measure in part["measures"]
        for event in measure["events"]
    ]
    events.sort(key=lambda e: e["onsetSeconds"])

    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    ticks_per_beat = mid.ticks_per_beat  # default 480
    tempo_us = mido.bpm2tempo(tempo_bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))

    midi_events = []
    for event in events:
        midi_events.append((event["onsetSeconds"], "on", event))
        midi_events.append((event["offsetSeconds"], "off", event))
    midi_events.sort(key=lambda e: (e[0], e[1] == "on"))

    seconds_per_tick = (tempo_us / 1_000_000) / ticks_per_beat
    last_tick = 0
    for seconds, kind, event in midi_events:
        tick = int(seconds / seconds_per_tick)
        delta = max(tick - last_tick, 0)
        last_tick = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        velocity = _DEFAULT_VELOCITY if kind == "on" else 0
        track.append(mido.Message(msg_type, note=event["pitch"], velocity=velocity, time=delta))

    mid.save(str(out_path))


def run_rederive_job(job_id: str) -> None:
    session: Session = _SessionLocal()
    storage = LocalStorageClient()
    try:
        job = session.get(TranscriptionJob, job_id)
        if job is None:
            logger.error("rederive job %s not found", job_id)
            return

        # Deterministic supersede rule: among all "rederive" job rows for
        # this project, this job is superseded only if a DIFFERENT job
        # outranks it by (created_at, id) — a strictly greater created_at,
        # or an equal created_at broken by a strictly greater id. SQLite
        # timestamps only carry second resolution, so two jobs enqueued in
        # the same second would otherwise tie on created_at alone and the
        # "newest wins" outcome could flip between runs depending on row
        # scan order; comparing id as an explicit, stable tiebreak removes
        # that nondeterminism regardless of how the query happens to order
        # ties. Tests must give same-project rederive jobs distinct
        # created_at values to exercise the ordinary (non-tied) path.
        newest = (
            session.query(TranscriptionJob)
            .filter(
                TranscriptionJob.project_id == job.project_id,
                TranscriptionJob.stage == "rederive",
            )
            .order_by(TranscriptionJob.created_at.desc(), TranscriptionJob.id.desc())
            .first()
        )
        is_superseded = newest is not None and newest.id != job.id and (
            newest.created_at > job.created_at
            or (newest.created_at == job.created_at and newest.id > job.id)
        )
        if is_superseded:
            job.status = "succeeded"
            job.progress = 100
            session.commit()
            return  # superseded — the newer job will re-derive the newer head

        job.status = "running"
        session.commit()

        project = session.get(Project, job.project_id)
        head_id = (project.settings or {}).get("scoreHeadRevisionId")
        revision = session.get(ScoreRevision, head_id) if head_id else None
        if revision is None:
            raise RuntimeError("rederive without a head revision")

        score = _reassign_with_locks(revision.score_json)
        revision.score_json = score
        flag_modified(revision, "score_json")
        # Commit the re-assigned score on its own, ahead of the export
        # attempt below. A failure in `except Exception` triggers
        # session.rollback(), which expires every object in the session —
        # any *uncommitted* change to `revision.score_json` would be
        # discarded and the next read would silently reload the pre-rederive
        # score. Exports are a re-derivable side effect of the score; the
        # score edit itself is not, so it must survive an export failure
        # (see test_rederive_export_failure_marks_job_failed_but_keeps_revision).
        session.commit()

        with tempfile.TemporaryDirectory() as tmp:
            midi_path = Path(tmp) / "output.mid"
            xml_path = Path(tmp) / "output.musicxml"
            _write_midi_from_score(score, midi_path)
            # Review fix: without `title=`, score_json_to_musicxml falls
            # back to "Untitled" (musicxml.export._apply_metadata) -- a
            # rederive's re-exported MusicXML was reverting a project's
            # real title to that fallback on every edit, since this call
            # site was never updated when export.py's own stage threaded
            # `ctx.job.project.title` through. `project` is already in
            # scope (fetched above for `head_id`).
            score_json_to_musicxml(score, xml_path, title=project.title)
            base = f"projects/{project.id}/exports/rev{revision.revision}"
            midi_key, xml_key = f"{base}/output.mid", f"{base}/output.musicxml"
            storage.put_bytes(midi_key, midi_path.read_bytes())
            storage.put_bytes(xml_key, xml_path.read_bytes())

        for export in session.query(Export).filter(Export.project_id == project.id).all():
            export.revision = revision.revision
            export.status = "succeeded"
            export.object_key = midi_key if export.format == "midi" else xml_key
        job.status = "succeeded"
        job.progress = 100
        session.commit()
    except Exception as exc:
        session.rollback()
        job = session.get(TranscriptionJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error_code = "INTERNAL_ERROR"
            job.error_detail = str(exc)[:500]
            session.commit()
        logger.exception("rederive job %s failed", job_id)
    finally:
        session.close()
