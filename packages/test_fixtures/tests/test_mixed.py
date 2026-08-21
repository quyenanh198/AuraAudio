import wave

import numpy as np
import pytest
from test_fixtures.mixed import (
    MixedClipSpec,
    generate_mixed_clip,
    synth_pad_interference,
    synth_percussion_interference,
    synth_vocal_interference,
)
from test_fixtures.reference import ReferenceClipSpec


def test_synth_vocal_interference_is_nonzero_and_bounded():
    sig = synth_vocal_interference(2.0, 22050, seed=1)
    assert len(sig) == int(2.0 * 22050)
    assert np.max(np.abs(sig)) > 0


def test_synth_percussion_interference_is_nonzero_and_bounded():
    sig = synth_percussion_interference(2.0, 22050, seed=1)
    assert len(sig) == int(2.0 * 22050)
    assert np.max(np.abs(sig)) > 0


def test_synth_pad_interference_is_nonzero_and_sustained():
    sig = synth_pad_interference(2.0, 22050, root_midi=48, seed=1)
    assert len(sig) == int(2.0 * 22050)
    # A sustained pad should have signal present throughout, not just at the start.
    first_half_energy = np.sum(sig[: len(sig) // 2] ** 2)
    second_half_energy = np.sum(sig[len(sig) // 2 :] ** 2)
    assert first_half_energy > 0
    assert second_half_energy > 0


def _melody_spec(instrument: str, timbre: str) -> ReferenceClipSpec:
    return ReferenceClipSpec(
        name=f"{instrument}_test_mixed",
        notes=[(60, 0.0, 0.4), (62, 0.5, 0.4), (64, 1.0, 0.4)],
        timbre=timbre,
        tempo_bpm=100.0,
        meter="4/4",
        key="C major",
        instrument=instrument,
    )


def test_generate_mixed_clip_ground_truth_matches_base_events(tmp_path):
    base = _melody_spec("guitar", "pluck")
    spec = MixedClipSpec(name="mixed_test", base=base, interference_kind="vocal_percussion")

    clip = generate_mixed_clip(spec, tmp_path / "mixed_test.wav")

    assert [e.pitch for e in clip.events] == [60, 62, 64]
    assert clip.path.exists()
    with wave.open(str(clip.path), "rb") as wav_file:
        assert wav_file.getnframes() > 0


def test_generate_mixed_clip_pad_interference(tmp_path):
    base = _melody_spec("guitar", "pluck")
    spec = MixedClipSpec(name="mixed_pad_test", base=base, interference_kind="pad")

    clip = generate_mixed_clip(spec, tmp_path / "mixed_pad_test.wav")

    assert clip.path.exists()
    assert len(clip.events) == 3


def test_generate_mixed_clip_rejects_unknown_interference_kind(tmp_path):
    base = _melody_spec("guitar", "pluck")
    spec = MixedClipSpec(name="bad", base=base, interference_kind="not-a-real-kind")

    with pytest.raises(ValueError):
        generate_mixed_clip(spec, tmp_path / "bad.wav")
