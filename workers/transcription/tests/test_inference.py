import pytest
from aura_api.models import MediaAsset, Project, TranscriptionJob
from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import inference
from score_schema.models import NoteEvent
from test_fixtures.generate import write_guitar_pluck_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def test_inference_detects_notes_in_fixture(db_session, sample_job, workdir):
    wav_path = workdir / "normalized.wav"
    write_guitar_pluck_wav(wav_path, duration_s=2.0, sample_rate=22050)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    notes = inference.run(ctx, normalized_path=wav_path)

    assert len(notes) > 0
    assert all(0 <= n.pitch <= 127 for n in notes)
    assert all(n.offset_s > n.onset_s for n in notes)


def test_inference_raises_no_music_detected_on_silence(db_session, sample_job, workdir):
    import numpy as np
    from scipy.io import wavfile

    silence = np.zeros(22050 * 2, dtype=np.int16)
    wav_path = workdir / "normalized.wav"
    wavfile.write(str(wav_path), 22050, silence)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    with pytest.raises(JobFailure) as exc_info:
        inference.run(ctx, normalized_path=wav_path)
    assert exc_info.value.code.value == "NO_MUSIC_DETECTED"


def test_inference_routes_piano_projects_to_the_piano_engine(monkeypatch, db_session, workdir):
    """Piano projects must call aura_worker.piano_engine.transcribe_piano,
    NOT basic-pitch -- mocked here (rather than running the real ~164MB
    model) so this stays a fast unit test; the real engine is exercised by
    test_piano_engine.py and the benchmark harness."""
    project = Project(owner_id="anonymous", title="P", instrument="piano")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/piano.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(
        project_id=project.id, media_asset_id=asset.id, input_hash="h2", status="queued"
    )
    db_session.add(job)
    db_session.commit()

    sentinel_notes = [NoteEvent(pitch=60, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.7)]
    calls = []

    def fake_transcribe_piano(path):
        calls.append(path)
        return sentinel_notes

    monkeypatch.setattr(inference, "transcribe_piano", fake_transcribe_piano)

    wav_path = workdir / "normalized.wav"
    # content irrelevant -- the engine call is mocked above
    write_guitar_pluck_wav(wav_path, duration_s=1.0, sample_rate=22050)

    ctx = StageContext(job=job, session=db_session, storage=FakeStorage(), workdir=workdir)
    notes = inference.run(ctx, normalized_path=wav_path)

    assert calls == [wav_path]
    assert notes == sentinel_notes
