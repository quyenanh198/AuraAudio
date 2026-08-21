from __future__ import annotations

import sys

import pytest
from aura_worker import piano_engine
from aura_worker.piano_engine import (
    PianoWeightsMissingError,
    _resolve_checkpoint_path,
    transcribe_piano,
)


def test_resolve_checkpoint_path_env_override_wins(monkeypatch, tmp_path):
    fake = tmp_path / "custom.pth"
    monkeypatch.setenv("AURA_PIANO_CHECKPOINT_PATH", str(fake))
    assert _resolve_checkpoint_path() == fake


def test_resolve_checkpoint_path_frozen_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.delenv("AURA_PIANO_CHECKPOINT_PATH", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    resolved = _resolve_checkpoint_path()
    assert resolved == tmp_path / "piano_weights" / "piano_transcription_crnn.pth"


def test_resolve_checkpoint_path_dev_mode_is_repo_relative(monkeypatch):
    monkeypatch.delenv("AURA_PIANO_CHECKPOINT_PATH", raising=False)
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    resolved = _resolve_checkpoint_path()
    assert resolved.parts[-3:] == ("weights", "piano", "piano_transcription_crnn.pth")


def test_transcribe_piano_raises_when_weights_missing(monkeypatch, tmp_path, workdir):
    missing = tmp_path / "does-not-exist.pth"
    monkeypatch.setenv("AURA_PIANO_CHECKPOINT_PATH", str(missing))
    monkeypatch.setattr(piano_engine, "_model", None)  # reset the lazy singleton between tests

    wav_path = workdir / "silence.wav"
    import numpy as np
    from scipy.io import wavfile

    wavfile.write(str(wav_path), 22050, np.zeros(22050, dtype="int16"))

    with pytest.raises(PianoWeightsMissingError):
        transcribe_piano(wav_path)


def test_transcribe_piano_converts_raw_events_to_note_events(monkeypatch, workdir):
    """Mocks the loaded model so this test doesn't need the real ~164MB
    checkpoint or a real torch inference pass -- it only exercises the
    NoteEvent conversion / confidence-proxy / offset-guard logic."""
    monkeypatch.setattr(piano_engine, "_model", None)

    class _FakeModel:
        def transcribe(self, audio, midi_path):
            return {
                "est_note_events": [
                    {"onset_time": 0.5, "offset_time": 0.9, "midi_note": 60, "velocity": 100},
                    # onset == offset: a zero-duration edge case the offset guard must fix up
                    {"onset_time": 1.0, "offset_time": 1.0, "midi_note": 64, "velocity": 0},
                ]
            }

    monkeypatch.setattr(piano_engine, "_load_model", lambda: _FakeModel())

    wav_path = workdir / "silence.wav"
    import numpy as np
    from scipy.io import wavfile

    wavfile.write(str(wav_path), 22050, np.zeros(22050, dtype="int16"))

    notes = transcribe_piano(wav_path)

    assert len(notes) == 2
    assert notes[0].pitch == 60
    assert notes[0].onset_s == 0.5
    assert notes[0].offset_s == 0.9
    assert notes[0].velocity == 100
    assert notes[0].confidence == pytest.approx(100 / 127.0)

    # Zero-duration note: offset must still be strictly greater than onset.
    assert notes[1].offset_s > notes[1].onset_s
    assert notes[1].confidence == 0.0
