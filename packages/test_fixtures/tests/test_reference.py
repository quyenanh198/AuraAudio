from pathlib import Path

import numpy as np
import pytest
from scipy.io import wavfile
from test_fixtures.reference import ReferenceClipSpec, generate_reference_clip


def _spec(**overrides) -> ReferenceClipSpec:
    defaults = dict(
        name="test_clip",
        notes=[(60, 0.0, 0.4), (64, 0.5, 0.4), (67, 1.0, 0.4)],
        timbre="pluck",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        instrument="guitar",
        sample_rate=22050,
    )
    defaults.update(overrides)
    return ReferenceClipSpec(**defaults)


def test_generate_reference_clip_writes_a_wav_file(tmp_path: Path):
    out_path = tmp_path / "clip.wav"
    generate_reference_clip(_spec(), out_path)

    assert out_path.exists()
    sr, data = wavfile.read(str(out_path))
    assert sr == 22050
    assert len(data) > 0


def test_generate_reference_clip_returns_ground_truth_events_matching_spec():
    spec = _spec()
    clip = generate_reference_clip(spec, Path("/tmp/unused_does_not_matter.wav"))

    assert len(clip.events) == 3
    assert [e.pitch for e in clip.events] == [60, 64, 67]
    assert clip.events[0].onset_s == 0.0
    assert clip.events[0].offset_s == 0.4
    assert clip.events[1].onset_s == 0.5


def test_generate_reference_clip_covers_full_duration_with_signal(tmp_path: Path):
    out_path = tmp_path / "clip.wav"
    clip = generate_reference_clip(_spec(), out_path)
    sr, data = wavfile.read(str(out_path))

    last_onset = max(e.onset_s for e in clip.events)
    assert len(data) / sr > last_onset


def test_generate_reference_clip_is_not_silent(tmp_path: Path):
    out_path = tmp_path / "clip.wav"
    generate_reference_clip(_spec(), out_path)
    _, data = wavfile.read(str(out_path))
    assert np.max(np.abs(data)) > 1000


def test_generate_reference_clip_supports_tone_timbre(tmp_path: Path):
    out_path = tmp_path / "clip.wav"
    clip = generate_reference_clip(_spec(timbre="tone", instrument="piano"), out_path)
    _, data = wavfile.read(str(out_path))
    assert np.max(np.abs(data)) > 1000
    assert clip.spec.timbre == "tone"


def test_generate_reference_clip_rejects_unknown_timbre(tmp_path: Path):
    out_path = tmp_path / "clip.wav"
    with pytest.raises(ValueError, match="banjo"):
        generate_reference_clip(_spec(timbre="banjo"), out_path)


def test_generate_reference_clip_handles_simultaneous_chord_notes(tmp_path: Path):
    out_path = tmp_path / "chord.wav"
    spec = _spec(notes=[(48, 0.0, 0.5), (52, 0.0, 0.5), (55, 0.0, 0.5)])
    clip = generate_reference_clip(spec, out_path)

    assert len(clip.events) == 3
    assert all(e.onset_s == 0.0 for e in clip.events)
    _, data = wavfile.read(str(out_path))
    assert np.max(np.abs(data)) > 1000


def test_generate_reference_clip_is_deterministic(tmp_path: Path):
    spec = _spec()
    generate_reference_clip(spec, tmp_path / "a.wav")
    generate_reference_clip(spec, tmp_path / "b.wav")

    _, data_a = wavfile.read(str(tmp_path / "a.wav"))
    _, data_b = wavfile.read(str(tmp_path / "b.wav"))
    np.testing.assert_array_equal(data_a, data_b)
