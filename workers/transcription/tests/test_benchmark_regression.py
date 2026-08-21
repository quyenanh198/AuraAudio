"""Regression guard for the detection-quality benchmark harness
(docs/superpowers/SESSION-HANDOFF.md's "Detection-quality roadmap", item 0).

Runs the REAL pipeline (ffmpeg + basic-pitch + librosa + music21 — no
mocking) against 3 of the curated benchmark_suite fixtures — a deliberate
subset, not the full ~10-fixture suite, to stay well under a couple of
minutes (see this file's `@pytest.mark.benchmark_regression` marker,
registered in pyproject.toml, for how to exclude this file if it ever
becomes a CI bottleneck: `-m "not benchmark_regression"`).

FLOOR is derived from docs/benchmarks/2026-08-21-baseline.json's measured
per-fixture onset F1 for these exact 3 fixtures (mean 0.6155), minus a
0.15 absolute margin — generous enough to absorb ordinary
platform/library-version noise while still catching a catastrophic
regression (e.g. a stage silently returning near-zero notes). This guards
against regressions ONLY; it does not replace the full benchmark run for
measuring actual improvement/regression magnitude — use
`python -m aura_worker.eval.benchmark` for that.

If a future roadmap item (docs/benchmarks' item 1+) intentionally and
verifiably improves onset F1, re-run the full benchmark, update
FLOOR to (new measured mean - 0.15), and note the change in this file's
comment trail — do not silently loosen the floor without a fresh
measurement backing it.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from aura_worker.eval import metrics
from aura_worker.eval.pipeline import run_pipeline_stages
from test_fixtures.benchmark_suite import get_benchmark_suite
from test_fixtures.reference import generate_reference_clip

_REGRESSION_FIXTURE_NAMES = (
    "guitar_two_voice_chords_a_minor_100",
    "guitar_arpeggio_a_minor_130",
    "piano_melody_c_major_100",
)
FLOOR = 0.45  # measured baseline mean 0.6155 - 0.15 margin; see module docstring


@pytest.mark.benchmark_regression
def test_aggregate_onset_f1_stays_above_baseline_floor():
    suite_by_name = {s.name: s for s in get_benchmark_suite()}
    missing = [name for name in _REGRESSION_FIXTURE_NAMES if name not in suite_by_name]
    assert not missing, (
        f"benchmark_suite no longer has fixtures {missing} -- update this test's selection"
    )

    f1_scores: list[float] = []
    with tempfile.TemporaryDirectory(prefix="aura_benchmark_regression_") as tmp:
        workdir = Path(tmp)
        for name in _REGRESSION_FIXTURE_NAMES:
            spec = suite_by_name[name]
            wav_path = workdir / f"{name}.wav"
            clip = generate_reference_clip(spec, wav_path)

            stage_workdir = workdir / f"{name}_stage"
            stage_workdir.mkdir()
            result = run_pipeline_stages(
                clip.path, instrument=spec.instrument, workdir=stage_workdir
            )

            f1 = metrics.onset_f1(clip.events, result.notes, onset_tolerance_s=0.05)
            f1_scores.append(f1.f1)

    aggregate_f1 = sum(f1_scores) / len(f1_scores)
    per_fixture = dict(zip(_REGRESSION_FIXTURE_NAMES, f1_scores, strict=True))
    assert aggregate_f1 >= FLOOR, (
        f"aggregate onset F1 {aggregate_f1:.3f} dropped below floor {FLOOR} "
        f"(per-fixture: {per_fixture}) -- "
        "this indicates a real detection-quality regression, not noise; see "
        "docs/benchmarks/2026-08-21-baseline.json for the full baseline."
    )
