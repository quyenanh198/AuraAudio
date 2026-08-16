from score_schema.models import NoteEvent
from score_schema.validate import validate_score

from aura_worker.stage_runner import StageContext
from aura_worker.stages import quantize
from aura_worker.stages.structure import StructureResult


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def _structure_120_four_four() -> StructureResult:
    return StructureResult(
        tempo_bpm=120.0, meter="4/4", key="C major",
        tempo_confidence=0.9, meter_confidence=0.8, key_confidence=0.7,
    )


def test_quantize_snaps_notes_to_sixteenth_grid_and_produces_valid_v4_score(db_session, sample_job, workdir):
    notes = [
        NoteEvent(pitch=64, onset_s=0.02, offset_s=0.48, velocity=90, confidence=0.9),
        NoteEvent(pitch=67, onset_s=0.53, offset_s=0.97, velocity=85, confidence=0.85),
    ]

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, _structure_120_four_four())

    validate_score(score)  # must not raise
    part = score["parts"][0]
    assert part["tempoBpm"] == 120.0
    assert part["meter"] == "4/4"
    assert part["key"] == "C major"
    assert part["confidence"] == {"tempo": 0.9, "meter": 0.8, "key": 0.7}
    events = part["measures"][0]["events"]
    assert events[0]["pitch"] == 64
    assert events[0]["notatedOnset"] == "0/1"
    assert events[0]["notatedDuration"] == "1/4"

    from aura_api.models import ScoreRevision
    revision = db_session.query(ScoreRevision).filter_by(project_id=sample_job.project_id).one()
    assert revision.revision == 0
    assert revision.score_json["schemaVersion"] == 4


def test_quantize_places_far_notes_in_later_measures(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=60, onset_s=9.0, offset_s=9.4, velocity=80, confidence=0.7)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, _structure_120_four_four())

    # 9.0s at 120 BPM (0.5s/beat) = beat 18 = measure 5 (4 beats/measure, 1-indexed)
    measure_numbers = [m["number"] for m in score["parts"][0]["measures"]]
    assert 5 in measure_numbers


def test_quantize_respects_three_four_measure_length(db_session, sample_job, workdir):
    structure = StructureResult(
        tempo_bpm=120.0, meter="3/4", key="C major",
        tempo_confidence=0.9, meter_confidence=0.8, key_confidence=0.7,
    )
    notes = [NoteEvent(pitch=60, onset_s=3.2, offset_s=3.5, velocity=80, confidence=0.7)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, structure)

    # seconds_per_beat = 0.5; onset_beats = 3.2/0.5 = 6.4, snapped to nearest
    # 1/4-beat = 6.5; measure_number = int(6.5 // 3) + 1 = 3 (3 beats/measure).
    measure_numbers = [m["number"] for m in score["parts"][0]["measures"]]
    assert measure_numbers == [3]
