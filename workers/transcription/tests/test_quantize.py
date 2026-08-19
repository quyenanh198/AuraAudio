import pytest

from score_schema.meters import SUPPORTED_METERS, beats_per_measure
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


@pytest.fixture
def quantize_harness(db_session, sample_job, workdir):
    """Runs quantize.run() for a given meter/tempo with notes placed at the
    given onset times (seconds); mirrors the ctx/storage wiring the
    hand-written tests above already use."""

    def _run(meter: str, tempo_bpm: float, notes_at_seconds: list[float]) -> dict:
        structure = StructureResult(
            tempo_bpm=tempo_bpm, meter=meter, key="C major",
            tempo_confidence=0.9, meter_confidence=0.8, key_confidence=0.7,
        )
        notes = [
            NoteEvent(pitch=60, onset_s=onset_s, offset_s=onset_s + 0.1, velocity=80, confidence=0.7)
            for onset_s in notes_at_seconds
        ]
        storage = FakeStorage()
        ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)
        return quantize.run(ctx, notes, structure)

    return _run


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
    # Measures 1-2 are pure silence and must now be emitted as empty-events
    # entries rather than dropped (silent-measure fidelity).
    measures = score["parts"][0]["measures"]
    assert [m["number"] for m in measures] == [1, 2, 3]
    assert measures[0]["events"] == []
    assert measures[1]["events"] == []
    assert len(measures[2]["events"]) == 1


def test_quantize_emits_empty_measures_for_leading_silence(db_session, sample_job, workdir):
    """First note lands in measure 3 (4/4, 120bpm) -> measures 1-2 must be
    emitted as empty-events entries, not omitted (would otherwise shift
    every later measure's musical content left when notated)."""
    notes = [NoteEvent(pitch=60, onset_s=9.0, offset_s=9.4, velocity=80, confidence=0.7)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, _structure_120_four_four())

    measures = score["parts"][0]["measures"]
    validate_score(score)
    assert [m["number"] for m in measures] == [1, 2, 3, 4, 5]
    assert measures[0]["events"] == []
    assert measures[1]["events"] == []
    assert measures[2]["events"] == []
    assert measures[3]["events"] == []
    assert len(measures[4]["events"]) == 1


def test_quantize_clears_stale_score_head_revision_pointer(db_session, sample_job, workdir):
    """Guards against a re-transcription leaving project.settings pointing at
    an old, now-orphaned ScoreRevision from a prior edit session: writing the
    new rev-0 must also clear any stale scoreHeadRevisionId so the API
    serves this fresh baseline instead of the stale edited revision."""
    from aura_api.models import Project

    project = db_session.get(Project, sample_job.project_id)
    project.settings = {"scoreHeadRevisionId": "some-old-revision-id"}
    db_session.commit()

    notes = [NoteEvent(pitch=64, onset_s=0.02, offset_s=0.48, velocity=90, confidence=0.9)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    quantize.run(ctx, notes, _structure_120_four_four())
    db_session.commit()

    db_session.refresh(project)
    assert "scoreHeadRevisionId" not in (project.settings or {})


def test_quantize_leaves_settings_untouched_when_no_head_pointer_set(db_session, sample_job, workdir):
    from aura_api.models import Project

    project = db_session.get(Project, sample_job.project_id)
    project.settings = {"someOtherKey": "kept"}
    db_session.commit()

    notes = [NoteEvent(pitch=64, onset_s=0.02, offset_s=0.48, velocity=90, confidence=0.9)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    quantize.run(ctx, notes, _structure_120_four_four())
    db_session.commit()

    db_session.refresh(project)
    assert project.settings == {"someOtherKey": "kept"}


def test_quantize_emits_empty_measures_for_interior_gap(db_session, sample_job, workdir):
    """A note in measure 1 and another in measure 4 (4/4, 120bpm) must
    leave measures 2-3 present as empty-events entries."""
    notes = [
        NoteEvent(pitch=60, onset_s=0.0, offset_s=0.4, velocity=80, confidence=0.7),
        NoteEvent(pitch=64, onset_s=6.0, offset_s=6.4, velocity=80, confidence=0.7),
    ]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, _structure_120_four_four())

    measures = score["parts"][0]["measures"]
    validate_score(score)
    assert [m["number"] for m in measures] == [1, 2, 3, 4]
    assert len(measures[0]["events"]) == 1
    assert measures[1]["events"] == []
    assert measures[2]["events"] == []
    assert len(measures[3]["events"]) == 1


@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_bucketing_matches_meter_length(meter, quantize_harness):
    # one note exactly at the start of what must be measure 2
    bpm = beats_per_measure(meter)
    onset_s = float(bpm) * 0.5  # tempo 120 -> quarter = 0.5s -> measure = bpm*0.5 s
    result = quantize_harness(meter=meter, tempo_bpm=120.0, notes_at_seconds=[onset_s])
    part = result["parts"][0]
    numbers = [m["number"] for m in part["measures"] if m["events"]]
    assert numbers == [2]
    event = next(m for m in part["measures"] if m["events"])["events"][0]
    assert event["notatedOnset"] == "0/1"


def test_silent_measures_emitted_for_6_8(quantize_harness):
    # note in measure 3 -> measures 1..3 all present, 1-2 empty
    onset_s = float(beats_per_measure("6/8")) * 0.5 * 2
    result = quantize_harness(meter="6/8", tempo_bpm=120.0, notes_at_seconds=[onset_s])
    part = result["parts"][0]
    assert [m["number"] for m in part["measures"]] == [1, 2, 3]
    assert part["measures"][0]["events"] == [] and part["measures"][1]["events"] == []


def test_silent_measures_emitted_for_5_4(quantize_harness):
    # spec §7 names 5/4 explicitly. note in measure 3 -> measures 1..3 all
    # present, 1-2 empty.
    onset_s = float(beats_per_measure("5/4")) * 0.5 * 2
    result = quantize_harness(meter="5/4", tempo_bpm=120.0, notes_at_seconds=[onset_s])
    part = result["parts"][0]
    assert [m["number"] for m in part["measures"]] == [1, 2, 3]
    assert part["measures"][0]["events"] == [] and part["measures"][1]["events"] == []


def test_stage_version_bumped():
    assert quantize.STAGE_VERSION == 4
