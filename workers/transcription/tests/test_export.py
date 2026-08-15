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


def test_export_stage_writes_midi_and_musicxml_and_creates_export_rows(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=64, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]
    score = build_score(
        instrument="guitar",
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
