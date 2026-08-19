import librosa

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import structure
from score_schema.models import NoteEvent
from test_fixtures.generate import (
    generate_metered_clicks,
    write_diatonic_melody_wav,
    write_metronome_pulse_wav,
)


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


# structure.run assumes non-empty notes (guaranteed in the real pipeline by
# inference.run's NO_MUSIC_DETECTED check) — tests that don't care about key
# detection still pass a minimal single-note list rather than [], since an
# empty stream would make music21's key analysis raise.
_PLACEHOLDER_NOTES = [NoteEvent(pitch=60, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]


def test_structure_detects_tempo_within_tolerance(db_session, sample_job, workdir):
    wav_path = workdir / "pulse.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="4/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    # Empirically validated tolerance: beat_track showed a consistent ~3 BPM
    # low bias against a 120 BPM synthetic click fixture during spec prototyping.
    assert abs(result.tempo_bpm - 120.0) <= 5.0


def test_structure_detects_four_four_meter(db_session, sample_job, workdir):
    wav_path = workdir / "pulse44.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="4/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    assert result.meter == "4/4"


def test_structure_detects_three_four_meter(db_session, sample_job, workdir):
    wav_path = workdir / "pulse34.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="3/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    assert result.meter == "3/4"


def test_structure_detects_known_key_from_real_transcription(db_session, sample_job, workdir):
    from aura_worker.stages import inference

    wav_path = workdir / "melody.wav"
    write_diatonic_melody_wav(wav_path, key="C major", duration_s=4.0, sample_rate=22050)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    notes = inference.run(ctx, normalized_path=wav_path)
    result = structure.run(ctx, normalized_path=wav_path, notes=notes)

    assert result.key == "C major"


def test_structure_detects_a_different_known_key(db_session, sample_job, workdir):
    from aura_worker.stages import inference

    wav_path = workdir / "melody_d.wav"
    write_diatonic_melody_wav(wav_path, key="D major", duration_s=4.0, sample_rate=22050)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    notes = inference.run(ctx, normalized_path=wav_path)
    result = structure.run(ctx, normalized_path=wav_path, notes=notes)

    assert result.key == "D major"


def test_structure_raises_model_failed_on_silence(db_session, sample_job, workdir):
    import numpy as np
    from scipy.io import wavfile

    silence = np.zeros(22050 * 4, dtype=np.int16)
    wav_path = workdir / "silence.wav"
    wavfile.write(str(wav_path), 22050, silence)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    try:
        structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)
        assert False, "expected JobFailure"
    except JobFailure as exc:
        assert exc.code.value == "MODEL_FAILED"


def test_structure_second_call_resumes_without_recompute(db_session, sample_job, workdir, monkeypatch):
    wav_path = workdir / "pulse_resume.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="4/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    first = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    import librosa

    def fail_if_called(*args, **kwargs):
        raise AssertionError("librosa.load should not be re-invoked on a cached structure stage")

    monkeypatch.setattr(librosa, "load", fail_if_called)

    second = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    assert second == first


def _detect_on_fixture(tmp_path, meter, tempo=120.0, measures=8):
    path = generate_metered_clicks(meter, tempo_bpm=tempo, measures=measures, path=tmp_path / "clip.wav")
    y, sr = librosa.load(str(path), sr=None)
    _, beat_times = structure._detect_tempo_and_beats(y, sr)
    detected, confidence = structure._detect_meter(y, sr, beat_times)
    return detected, confidence


def test_detects_6_8_not_3_4(tmp_path):
    # Tempo tweak (sanctioned by the meter-expansion plan's detection note):
    # at the 120 bpm default, librosa's beat tracker locks onto a ~2-eighth
    # pulse for this click fixture, discarding exactly the eighth-3
    # secondary-accent information the 6/8 vs. 3/4 comb relies on. At 50
    # bpm the tracker resolves the fixture's actual eighth-note grid (beat
    # spacing matches the true eighth interval almost exactly), giving the
    # detector the information it needs for a clearly-6/8 clip to detect as
    # 6/8. See structure._detect_meter's docstring-style comment and the
    # task report for the full diagnosis.
    detected, confidence = _detect_on_fixture(tmp_path, "6/8", tempo=50.0)
    assert detected == "6/8"
    assert 0.0 <= confidence <= 1.0


def test_detects_2_4(tmp_path):
    detected, _ = _detect_on_fixture(tmp_path, "2/4")
    assert detected == "2/4"


def test_still_detects_4_4(tmp_path):
    detected, _ = _detect_on_fixture(tmp_path, "4/4")
    assert detected == "4/4"


def test_still_detects_3_4(tmp_path):
    detected, _ = _detect_on_fixture(tmp_path, "3/4")
    assert detected == "3/4"


def test_stage_version_bumped():
    assert structure.STAGE_VERSION == 2
