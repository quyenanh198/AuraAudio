"""Detection-quality roadmap item 3 benchmark: opt-in source separation
("isolate instrument from mix") before inference.

Run:
    uv run --package aura-worker python \\
        workers/transcription/scripts/dq3_mixed_benchmark.py --out docs/benchmarks

Requires the demucs weights present locally first:
    uv run --package aura-worker python \\
        workers/transcription/scripts/fetch_demucs_weights.py

Measures three things, all against the REAL pipeline (aura_worker.eval.pipeline,
same in-process stage execution the main benchmark harness uses -- no mocks):

1. Mixed fixtures (test_fixtures.mixed_benchmark, instrument + synthesized
   vocal/percussion/pad interference): onset F1 with separation OFF vs ON,
   for both guitar (the shipped, evidence-backed case) and piano (kept
   separation OFF in the real app -- included here anyway, as the negative
   evidence for that decision, not because piano separation ships).
2. Clean fixtures (the main curated guitar suite, test_fixtures.
   benchmark_suite): onset F1 with separation OFF vs ON, to confirm
   separation doesn't hurt when there's nothing to separate.
3. Real CPU wall-clock timing for a 30s synthesized mix, to ground the
   "how slow is this" UX claim in a real measurement rather than a guess.

Writes docs/benchmarks/{date}-dq3.md and .json.
"""
from __future__ import annotations

import os
import tempfile

_BENCHMARK_DATA_DIR = tempfile.mkdtemp(prefix="aura_dq3_benchmark_")
os.environ["DATABASE_URL"] = f"sqlite:///{_BENCHMARK_DATA_DIR}/benchmark.db"
os.environ["AURA_DATA_DIR"] = _BENCHMARK_DATA_DIR

import argparse  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from dataclasses import asdict, dataclass  # noqa: E402
from datetime import UTC, date, datetime  # noqa: E402
from pathlib import Path  # noqa: E402

from aura_worker.eval import metrics  # noqa: E402
from aura_worker.eval.pipeline import run_pipeline_stages  # noqa: E402

ONSET_TOLERANCE_S = 0.05


def _git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except Exception:
        return "unknown"


@dataclass
class MixedFixtureResult:
    name: str
    instrument: str
    interference_kind: str
    truth_note_count: int
    onset_f1_off: float
    onset_f1_on: float
    delta: float
    detected_notes_off: int
    detected_notes_on: int
    error_off: str | None = None
    error_on: str | None = None


def _score_mixed_fixture(spec, workdir: Path) -> MixedFixtureResult:
    from test_fixtures.mixed import generate_mixed_clip

    wav_path = workdir / f"{spec.name}.wav"
    clip = generate_mixed_clip(spec, wav_path)
    instrument = spec.base.instrument

    def _run(separate_source: bool) -> tuple[float, int, str | None]:
        stage_wd = workdir / f"{spec.name}_{'on' if separate_source else 'off'}"
        stage_wd.mkdir(parents=True, exist_ok=True)
        try:
            result = run_pipeline_stages(
                clip.path, instrument=instrument, workdir=stage_wd, separate_source=separate_source
            )
        except Exception as exc:
            return 0.0, 0, f"{type(exc).__name__}: {exc}"
        f1 = metrics.onset_f1(clip.events, result.notes, onset_tolerance_s=ONSET_TOLERANCE_S)
        return f1.f1, len(result.notes), None

    f1_off, n_off, err_off = _run(False)
    f1_on, n_on, err_on = _run(True)

    return MixedFixtureResult(
        name=spec.name,
        instrument=instrument,
        interference_kind=spec.interference_kind,
        truth_note_count=len(clip.events),
        onset_f1_off=f1_off,
        onset_f1_on=f1_on,
        delta=f1_on - f1_off,
        detected_notes_off=n_off,
        detected_notes_on=n_on,
        error_off=err_off,
        error_on=err_on,
    )


@dataclass
class CleanFixtureResult:
    name: str
    onset_f1_off: float
    onset_f1_on: float
    delta: float


def _score_clean_guitar_fixture(spec, workdir: Path) -> CleanFixtureResult:
    from test_fixtures.reference import generate_reference_clip

    wav_path = workdir / f"{spec.name}.wav"
    clip = generate_reference_clip(spec, wav_path)

    def _run(separate_source: bool) -> float:
        stage_wd = workdir / f"{spec.name}_{'on' if separate_source else 'off'}"
        stage_wd.mkdir(parents=True, exist_ok=True)
        result = run_pipeline_stages(
            clip.path, instrument="guitar", workdir=stage_wd, separate_source=separate_source
        )
        return metrics.onset_f1(clip.events, result.notes, onset_tolerance_s=ONSET_TOLERANCE_S).f1

    f1_off = _run(False)
    f1_on = _run(True)
    return CleanFixtureResult(name=spec.name, onset_f1_off=f1_off, onset_f1_on=f1_on, delta=f1_on - f1_off)


def _time_separation_on_synthetic_clip(duration_s: float, workdir: Path) -> dict:
    """Real wall-clock timing: synthesizes a `duration_s` guitar-pluck clip
    (silent-ish, content doesn't matter for timing) and runs
    aura_worker.separation.separate_guitar directly against the real,
    build-time-fetched weights."""
    import numpy as np
    from scipy.io import wavfile

    from aura_worker.separation import separate_guitar

    sample_rate = 44100
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype(np.int16)
    src_path = workdir / "timing_source.wav"
    wavfile.write(str(src_path), sample_rate, signal)

    t0 = time.monotonic()
    separate_guitar(src_path, workdir / "timing_out.wav")
    elapsed_s = time.monotonic() - t0
    return {"clip_duration_s": duration_s, "elapsed_s": elapsed_s}


def run_dq3_benchmark() -> dict:
    from test_fixtures.benchmark_suite import get_benchmark_suite
    from test_fixtures.mixed_benchmark import MIXED_BENCHMARK_SUITE_VERSION, get_mixed_benchmark_suite

    mixed_results: list[MixedFixtureResult] = []
    clean_results: list[CleanFixtureResult] = []

    with tempfile.TemporaryDirectory(prefix="aura_dq3_run_") as tmp:
        workdir = Path(tmp)

        for spec in get_mixed_benchmark_suite():
            mixed_results.append(_score_mixed_fixture(spec, workdir))

        clean_guitar_specs = [s for s in get_benchmark_suite() if s.instrument == "guitar"]
        for spec in clean_guitar_specs:
            clean_results.append(_score_clean_guitar_fixture(spec, workdir))

        timing_30s = _time_separation_on_synthetic_clip(30.0, workdir)
        timing_60s = _time_separation_on_synthetic_clip(60.0, workdir)

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "commit": _git_commit(),
        "mixed_suite_version": MIXED_BENCHMARK_SUITE_VERSION,
        "mixed_results": [asdict(r) for r in mixed_results],
        "clean_guitar_results": [asdict(r) for r in clean_results],
        "timing": {"clip_30s": timing_30s, "clip_60s": timing_60s},
    }


def _build_markdown(report: dict) -> str:
    lines = ["# DQ-3: opt-in source separation benchmark", ""]
    lines.append(
        "Detection-quality roadmap item 3 (docs/superpowers/SESSION-HANDOFF.md "
        '"Detection-quality roadmap"). Measures separation ON vs OFF on mixed '
        "(instrument + interference) fixtures and on the clean guitar suite, "
        "plus real CPU timing."
    )
    lines.append("")
    lines.append(f"- **Generated:** {report['generated_at']}")
    lines.append(f"- **Commit:** `{report['commit']}`")
    lines.append(f"- **Mixed fixture-suite version:** `{report['mixed_suite_version']}`")
    lines.append("")

    lines.append("## Mixed fixtures: separation OFF vs ON")
    lines.append("")
    lines.append("| Fixture | Instrument | Interference | Truth notes | F1 OFF | F1 ON | Δ | Notes OFF | Notes ON |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in report["mixed_results"]:
        lines.append(
            f"| {r['name']} | {r['instrument']} | {r['interference_kind']} | {r['truth_note_count']} "
            f"| {r['onset_f1_off']:.3f} | {r['onset_f1_on']:.3f} | {r['delta']:+.3f} "
            f"| {r['detected_notes_off']} | {r['detected_notes_on']} |"
        )
    lines.append("")

    lines.append("## Clean guitar suite: separation OFF vs ON (must not regress)")
    lines.append("")
    lines.append("| Fixture | F1 OFF | F1 ON | Δ |")
    lines.append("|---|---|---|---|")
    for r in report["clean_guitar_results"]:
        lines.append(f"| {r['name']} | {r['onset_f1_off']:.3f} | {r['onset_f1_on']:.3f} | {r['delta']:+.3f} |")
    lines.append("")

    t30 = report["timing"]["clip_30s"]
    t60 = report["timing"]["clip_60s"]
    lines.append("## CPU timing (real, this machine)")
    lines.append("")
    lines.append(f"- 30s clip: {t30['elapsed_s']:.2f}s separation compute time")
    lines.append(f"- 60s clip: {t60['elapsed_s']:.2f}s separation compute time")
    lines.append("")

    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="output directory for the report")
    args = parser.parse_args(argv)

    report = run_dq3_benchmark()

    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"{date.today().isoformat()}-dq3"
    md_path = args.out / f"{stem}.md"
    json_path = args.out / f"{stem}.json"

    md_path.write_text(_build_markdown(report))
    json_path.write_text(json.dumps(report, indent=2) + "\n")

    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
