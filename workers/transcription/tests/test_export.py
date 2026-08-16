import mido
import pytest

from score_schema.models import NoteEvent, build_score

from aura_worker.stage_runner import StageContext
from aura_worker.stages import export as export_stage


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def _sample_score(tempo_bpm: float = 120.0):
    return build_score(
        instrument="guitar",
        tempo_bpm=tempo_bpm,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [{
                "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                "confidence": 0.9, "locked": False,
            }],
        }],
    )


def test_export_stage_writes_midi_and_musicxml_and_creates_export_rows(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=64, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]
    score = _sample_score()

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = export_stage.run(ctx, notes=notes, score=score)

    assert result["midi_key"].endswith(".mid")
    assert result["musicxml_key"].endswith(".musicxml")
    assert any(k == result["midi_key"] for k in storage.objects)
    assert any(k == result["musicxml_key"] for k in storage.objects)

    from aura_api.models import Export, TranscriptionJob

    exports = db_session.query(Export).filter_by(job_id=sample_job.id).all()
    formats = {e.format for e in exports}
    assert formats == {"midi", "musicxml"}
    assert all(e.status == "succeeded" for e in exports)

    refreshed_job = db_session.get(TranscriptionJob, sample_job.id)
    assert refreshed_job.status == "succeeded"


def test_export_stage_midi_uses_detected_tempo(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=64, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]
    score = _sample_score(tempo_bpm=90.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = export_stage.run(ctx, notes=notes, score=score)

    midi_bytes = storage.objects[result["midi_key"]]
    (workdir / "check.mid").write_bytes(midi_bytes)
    mid = mido.MidiFile(str(workdir / "check.mid"))
    tempo_messages = [msg for track in mid.tracks for msg in track if msg.type == "set_tempo"]
    assert len(tempo_messages) == 1
    assert mido.tempo2bpm(tempo_messages[0].tempo) == pytest.approx(90.0, abs=0.01)
