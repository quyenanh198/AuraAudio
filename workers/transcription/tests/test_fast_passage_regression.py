"""Regression guard for the fast-passage / MIN_DURATION_S re-derivation
(post-review follow-up to detection-quality roadmap item 1 -- see
`aura_worker.ghost_filter`'s "RE-DERIVATION" docstring paragraph and
`docs/benchmarks/2026-08-21-dq1b.md`).

Runs the REAL pipeline (ffmpeg + basic-pitch, no mocking) against the two
16th-note-run fixtures added specifically to stress
`aura_worker.ghost_filter.MIN_DURATION_S` against a genuinely fast, short
real note (nominal note length ~0.091s, well under the 0.15s floor). Two
things are asserted, deliberately kept separate so a future regression
points at the right cause:

1. `filter_ghost_notes` does not delete any of basic-pitch's own true
   positives on these fixtures -- i.e. the duration floor specifically is
   not what limits fast-passage recall (locks in the RE-DERIVATION
   finding: raw basic-pitch note durations on these fixtures never came
   close to the 0.15s floor in either direction).
2. Onset F1 on each fixture stays at or above its last-measured value
   (with a margin) -- a coarser guard against silent regressions in
   `aura_worker.instrument_thresholds` or `aura_worker.ghost_filter`
   generally, without asserting these fixtures reach "good" F1 (piano's
   0.476 here is a known, disclosed, NOT-fixed-here basic-pitch
   onset-merging limitation, not a bug this test should paper over).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from aura_worker.eval import metrics
from aura_worker.eval.pipeline import run_pipeline_stages
from aura_worker.ghost_filter import filter_ghost_notes
from test_fixtures.benchmark_suite import get_benchmark_suite
from test_fixtures.reference import generate_reference_clip

# Measured via docs/benchmarks/2026-08-21-dq1b.md; a small absolute margin
# below each so ordinary platform/library-version noise doesn't trip this
# guard for reasons unrelated to a real regression.
_FIXTURE_ONSET_F1_FLOORS = {
    "guitar_sixteenth_run_c_major_140": 0.75,  # measured 0.857
    "piano_sixteenth_run_c_major_140": 0.35,  # measured 0.476
}


@pytest.mark.benchmark_regression
@pytest.mark.parametrize("fixture_name", sorted(_FIXTURE_ONSET_F1_FLOORS))
def test_fast_passage_onset_f1_stays_above_floor(fixture_name):
    suite_by_name = {s.name: s for s in get_benchmark_suite()}
    assert fixture_name in suite_by_name, (
        f"benchmark_suite no longer has fixture {fixture_name!r} -- update this test's selection"
    )
    spec = suite_by_name[fixture_name]

    with tempfile.TemporaryDirectory(prefix="aura_fast_passage_regression_") as tmp:
        workdir = Path(tmp)
        wav_path = workdir / f"{fixture_name}.wav"
        clip = generate_reference_clip(spec, wav_path)

        stage_workdir = workdir / f"{fixture_name}_stage"
        stage_workdir.mkdir()
        result = run_pipeline_stages(clip.path, instrument=spec.instrument, workdir=stage_workdir)

    f1 = metrics.onset_f1(clip.events, result.notes, onset_tolerance_s=0.05)
    floor = _FIXTURE_ONSET_F1_FLOORS[fixture_name]
    assert f1.f1 >= floor, (
        f"{fixture_name} onset F1 {f1.f1:.3f} dropped below floor {floor} -- "
        "see docs/benchmarks/2026-08-21-dq1b.md for the last measured value."
    )


@pytest.mark.benchmark_regression
def test_ghost_filter_duration_floor_is_not_the_bottleneck_on_a_fast_passage():
    """Directly verifies the RE-DERIVATION claim in aura_worker.ghost_filter's
    docstring: on the fast-passage fixtures, filter_ghost_notes' duration
    floor is not what limits recall -- basic-pitch's own raw output never
    comes close to it. Runs basic-pitch's real predict() at the tuned
    guitar thresholds directly (bypassing the cached-artifact stage
    machinery, since this test wants the RAW pre-filter notes, not just
    the pipeline's final filtered ones)."""
    from aura_worker.instrument_thresholds import thresholds_for_instrument
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict
    from score_schema.models import NoteEvent

    from aura_worker.stages.normalize import TARGET_SAMPLE_RATE
    import subprocess

    suite_by_name = {s.name: s for s in get_benchmark_suite()}
    spec = suite_by_name["guitar_sixteenth_run_c_major_140"]

    with tempfile.TemporaryDirectory(prefix="aura_fast_passage_raw_") as tmp:
        workdir = Path(tmp)
        wav_path = workdir / "clip.wav"
        clip = generate_reference_clip(spec, wav_path)
        norm_path = workdir / "norm.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(clip.path),
                "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE),
                "-af", "loudnorm=I=-23:TP=-2:LRA=7",
                str(norm_path),
            ],
            capture_output=True, timeout=120, check=True,
        )
        thresholds = thresholds_for_instrument(spec.instrument)
        _, _, note_events = predict(
            str(norm_path),
            model_or_model_path=ICASSP_2022_MODEL_PATH,
            onset_threshold=thresholds.onset_threshold,
            frame_threshold=thresholds.frame_threshold,
        )

    raw_notes = [
        NoteEvent(
            pitch=int(round(pitch_midi)), onset_s=float(start_s), offset_s=float(end_s),
            velocity=int(round(min(max(amplitude, 0.0), 1.0) * 127)),
            confidence=float(min(max(amplitude, 0.0), 1.0)),
        )
        for start_s, end_s, pitch_midi, amplitude, *_rest in note_events
    ]
    filtered_notes = filter_ghost_notes(raw_notes)

    raw_f1 = metrics.onset_f1(clip.events, raw_notes)
    filtered_f1 = metrics.onset_f1(clip.events, filtered_notes)
    # If the duration floor were deleting true positives here, filtering
    # would measurably hurt recall/F1 relative to the raw output. It
    # shouldn't -- basic-pitch's own onset detection, not this filter, is
    # what limits recall on this fixture (see ghost_filter's docstring).
    assert filtered_f1.f1 >= raw_f1.f1 - 1e-9, (
        f"filtering dropped onset F1 from {raw_f1.f1:.3f} to {filtered_f1.f1:.3f} on a "
        "fast-passage fixture -- MIN_DURATION_S may be deleting genuine fast notes; "
        "re-derive it per aura_worker.ghost_filter's RE-DERIVATION note."
    )
