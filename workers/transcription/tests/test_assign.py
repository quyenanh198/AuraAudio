# workers/transcription/tests/test_assign.py
from score_schema.models import build_score
from score_schema.validate import validate_score

from aura_worker.stage_runner import StageContext
from aura_worker.stages import assign


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def _guitar_score():
    return build_score(
        instrument="guitar",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [
                {
                    "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
            ],
        }],
    )


def test_assign_stage_sets_string_and_fret_for_guitar(db_session, sample_job, workdir):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = assign.run(ctx, _guitar_score())

    event = result["parts"][0]["measures"][0]["events"][0]
    assert event["string"] is not None
    assert event["fret"] is not None
    validate_score(result)  # must not raise — v3-shaped output


def test_assign_stage_second_call_resumes_without_recompute(db_session, sample_job, workdir, monkeypatch):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    first = assign.run(ctx, _guitar_score())

    def fail_if_called(*args, **kwargs):
        raise AssertionError("assign_measure should not be re-invoked on a cached assign stage")

    # assign.py does `from aura_worker.fingering import assign_measure`, which
    # binds the name into assign.py's own module namespace at import time —
    # patching aura_worker.fingering.assign_measure afterward would NOT affect
    # that already-bound reference. Patch it where assign.py actually looks it
    # up: aura_worker.stages.assign.assign_measure.
    monkeypatch.setattr(assign, "assign_measure", fail_if_called)

    second = assign.run(ctx, _guitar_score())
    assert second == first


def _piano_score():
    return build_score(
        instrument="piano",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [
                {
                    "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
            ],
        }],
    )


def test_assign_stage_piano_passthrough(db_session, sample_job, workdir):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = assign.run(ctx, _piano_score())

    event = result["parts"][0]["measures"][0]["events"][0]
    assert event["string"] is None
    assert event["fret"] is None
    validate_score(result)
