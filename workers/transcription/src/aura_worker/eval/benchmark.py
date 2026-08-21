"""CLI entrypoint for the transcription detection-quality benchmark.

Run:
    uv run --package aura-worker python -m aura_worker.eval.benchmark --out docs/benchmarks
    uv run --package aura-worker python -m aura_worker.eval.benchmark \
        --out docs/benchmarks --manifest my_manifest.json

For each fixture (the curated synthetic suite, plus any `--manifest` real
recordings), runs the real normalize -> inference -> structure -> quantize
stages in-process (aura_worker.eval.pipeline) and scores the result against
ground truth (aura_worker.eval.metrics): note onset F1 (50ms tolerance),
onset+offset F1, tempo (±5%), key (exact), meter (exact). Writes a
human-readable Markdown report and a machine-readable JSON report to
`--out`, named `{date}-{label}.{md,json}` (default label "baseline").

Measurement only: this script never modifies pipeline behavior, and
performs no network I/O (basic-pitch's weights are already local, per
this project's offline rule).

IMPORTANT: DATABASE_URL / AURA_DATA_DIR are pointed at a fresh temp
directory as the very first thing this module does, BEFORE any aura_api /
aura_worker.stages import -- aura_api.db creates its engine at import
time from those env vars, and this script must never touch the real
app's ./data database (mirrors workers/transcription/tests/conftest.py's
identical guard).
"""
from __future__ import annotations

import os
import tempfile

_BENCHMARK_DATA_DIR = tempfile.mkdtemp(prefix="aura_benchmark_")
os.environ["DATABASE_URL"] = f"sqlite:///{_BENCHMARK_DATA_DIR}/benchmark.db"
os.environ["AURA_DATA_DIR"] = _BENCHMARK_DATA_DIR

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from datetime import UTC, date, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from aura_worker.eval import metrics  # noqa: E402
from aura_worker.eval.manifest import load_manifest, load_reference_events_from_midi  # noqa: E402
from aura_worker.eval.pipeline import run_pipeline_stages  # noqa: E402
from aura_worker.eval.report import (  # noqa: E402
    BenchmarkReport,
    FixtureResult,
    build_json_report,
    build_markdown_report,
    compute_aggregate,
)

TEMPO_REL_TOL = 0.05
ONSET_TOLERANCE_S = 0.05


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


def _score_synthetic_fixture(spec, workdir: Path) -> FixtureResult:
    from test_fixtures.reference import generate_reference_clip

    wav_path = workdir / f"{spec.name}.wav"
    clip = generate_reference_clip(spec, wav_path)
    stage_workdir = workdir / f"{spec.name}_stage"
    stage_workdir.mkdir(parents=True, exist_ok=True)

    try:
        result = run_pipeline_stages(clip.path, instrument=spec.instrument, workdir=stage_workdir)
    except Exception as exc:  # any stage's JobFailure or unexpected error -- still report it
        return FixtureResult(
            name=spec.name,
            source="synthetic",
            instrument=spec.instrument,
            truth_tempo_bpm=spec.tempo_bpm,
            truth_meter=spec.meter,
            truth_key=spec.key,
            error=f"{type(exc).__name__}: {exc}",
        )

    return _build_fixture_result(
        name=spec.name,
        source="synthetic",
        instrument=spec.instrument,
        truth_tempo_bpm=spec.tempo_bpm,
        truth_meter=spec.meter,
        truth_key=spec.key,
        reference_events=clip.events,
        result=result,
    )


def _score_manifest_entry(entry, workdir: Path) -> FixtureResult:
    stage_workdir = workdir / f"manifest_{entry.name}"
    stage_workdir.mkdir(parents=True, exist_ok=True)

    try:
        reference_events = load_reference_events_from_midi(entry.reference_midi_path)
        result = run_pipeline_stages(
            entry.audio_path, instrument=entry.instrument, workdir=stage_workdir
        )
    except Exception as exc:
        return FixtureResult(
            name=entry.name,
            source="manifest",
            instrument=entry.instrument,
            truth_tempo_bpm=None,
            truth_meter=None,
            truth_key=None,
            error=f"{type(exc).__name__}: {exc}",
        )

    return _build_fixture_result(
        name=entry.name,
        source="manifest",
        instrument=entry.instrument,
        # a manifest entry supplies reference MIDI, not tempo/key/meter ground truth
        truth_tempo_bpm=None,
        truth_meter=None,
        truth_key=None,
        reference_events=reference_events,
        result=result,
    )


def _build_fixture_result(
    *, name, source, instrument, truth_tempo_bpm, truth_meter, truth_key, reference_events, result
) -> FixtureResult:
    onset_f1 = metrics.onset_f1(reference_events, result.notes, onset_tolerance_s=ONSET_TOLERANCE_S)
    onset_offset_f1 = metrics.onset_offset_f1(
        reference_events, result.notes, onset_tolerance_s=ONSET_TOLERANCE_S
    )

    tempo_ok = (
        metrics.tempo_within_tolerance(result.tempo_bpm, truth_tempo_bpm, rel_tol=TEMPO_REL_TOL)
        if truth_tempo_bpm is not None
        else None
    )
    meter_ok = metrics.meter_matches(result.meter, truth_meter) if truth_meter is not None else None
    key_ok = metrics.key_matches(result.key, truth_key) if truth_key is not None else None

    return FixtureResult(
        name=name,
        source=source,
        instrument=instrument,
        truth_tempo_bpm=truth_tempo_bpm,
        truth_meter=truth_meter,
        truth_key=truth_key,
        detected_tempo_bpm=result.tempo_bpm,
        detected_meter=result.meter,
        detected_key=result.key,
        tempo_ok=tempo_ok,
        meter_ok=meter_ok,
        key_ok=key_ok,
        onset_f1=onset_f1,
        onset_offset_f1=onset_offset_f1,
    )


def run_benchmark(manifest_path: Path | None = None) -> BenchmarkReport:
    """Runs the full benchmark (curated suite + optional manifest) and
    returns the assembled report. Split out from main() so tests / other
    callers can invoke it without going through argv/sys.exit."""
    from test_fixtures.benchmark_suite import BENCHMARK_SUITE_VERSION, get_benchmark_suite

    fixture_results: list[FixtureResult] = []

    with tempfile.TemporaryDirectory(prefix="aura_benchmark_run_") as tmp:
        workdir = Path(tmp)
        for spec in get_benchmark_suite():
            fixture_results.append(_score_synthetic_fixture(spec, workdir))

        if manifest_path is not None:
            for entry in load_manifest(manifest_path):
                fixture_results.append(_score_manifest_entry(entry, workdir))

    return BenchmarkReport(
        generated_at=datetime.now(UTC).isoformat(),
        commit=_git_commit(),
        suite_version=BENCHMARK_SUITE_VERSION,
        fixture_results=fixture_results,
        aggregate=compute_aggregate(fixture_results),
        manifest_path=str(manifest_path) if manifest_path is not None else None,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", required=True, type=Path, help="output directory for the report"
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="optional path to a local real-recording manifest JSON",
    )
    parser.add_argument(
        "--label", default="baseline", help="report filename label (default: 'baseline')"
    )
    args = parser.parse_args(argv)

    start = time.monotonic()
    report = run_benchmark(manifest_path=args.manifest)
    elapsed_s = time.monotonic() - start

    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"{date.today().isoformat()}-{args.label}"
    md_path = args.out / f"{stem}.md"
    json_path = args.out / f"{stem}.json"

    markdown = build_markdown_report(report)
    markdown += f"\n\n_Full benchmark run took {elapsed_s:.1f}s._\n"
    md_path.write_text(markdown)
    json_path.write_text(json.dumps(build_json_report(report), indent=2) + "\n")

    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    print(f"Elapsed: {elapsed_s:.1f}s")
    if report.aggregate is not None:
        print(
            f"Mean onset F1: {report.aggregate.mean_onset_f1:.3f}  "
            f"tempo acc: {report.aggregate.tempo_accuracy:.1%}  "
            f"key acc: {report.aggregate.key_accuracy:.1%}  "
            f"meter acc: {report.aggregate.meter_accuracy:.1%}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
