"""Rerunnable evidence for `aura_worker.instrument_thresholds`'s per-instrument
basic-pitch `onset_threshold`/`frame_threshold` values.

Detection-quality roadmap item 1 (docs/superpowers/SESSION-HANDOFF.md
"Detection-quality roadmap"), added after code review flagged that the
claim "confirmed by a finer grid, not sweep noise" (piano
`frame_threshold=0.1`) cited a table that did not exist anywhere in the
repository. This script IS that table's source: run it to reproduce the
grid search from scratch, or read
`docs/benchmarks/2026-08-21-threshold-sweep.md` for its last recorded
output.

Run:
    uv run --package aura-worker python \\
        workers/transcription/scripts/tune_instrument_thresholds.py \\
        --out docs/benchmarks/2026-08-21-threshold-sweep.md

Methodology: for each instrument, for each `(onset_threshold,
frame_threshold)` candidate, runs basic-pitch's real `predict()` (via the
same real, offline-bundled ICASSP 2022 model the production pipeline
uses) against every same-instrument fixture in the curated benchmark
suite, normalized exactly as `aura_worker.stages.normalize.run` does
(same ffmpeg invocation, duplicated here rather than imported so this
stays a standalone, dependency-light research script), then scores onset
F1 (`aura_worker.eval.metrics.onset_f1`) both on the raw basic-pitch
output and after `aura_worker.ghost_filter.filter_ghost_notes` -- exactly
how the real pipeline uses both together in `inference.run`. Only the
filtered column is what production behavior reflects; the raw column is
kept for transparency about how much of the gain is basic-pitch's own
thresholds vs. the ghost filter.

Caveat inherited from the wider DQ-1 methodology (see
docs/superpowers/SESSION-HANDOFF.md's item 1 entry): every value here is
tuned and gated on the same 12-fixture synthetic suite it's scored
against -- there is no held-out set, and no real-recording manifest run
has validated these thresholds yet. Treat this as evidence the chosen
values are a real, non-noise optimum ON THIS SUITE, not a guarantee they
generalize to real recordings.
"""
from __future__ import annotations

import argparse
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

TARGET_SAMPLE_RATE = 22050  # must match aura_worker.stages.normalize.TARGET_SAMPLE_RATE


def _normalize(src: Path, dst: Path) -> None:
    """Mirrors aura_worker.stages.normalize.run's ffmpeg invocation exactly
    (duplicated, not imported, to keep this script runnable without a
    StageContext/DB -- see module docstring)."""
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(src),
            "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE),
            "-af", "loudnorm=I=-23:TP=-2:LRA=7",
            str(dst),
        ],
        capture_output=True, timeout=120, check=True,
    )


@dataclass(frozen=True)
class SweepResult:
    onset_threshold: float
    frame_threshold: float
    per_fixture_f1: dict[str, float]  # post-ghost-filter onset F1
    per_fixture_raw_f1: dict[str, float]  # pre-ghost-filter onset F1

    @property
    def mean_f1(self) -> float:
        return sum(self.per_fixture_f1.values()) / len(self.per_fixture_f1)

    @property
    def min_f1(self) -> float:
        return min(self.per_fixture_f1.values())


def _prepare_fixtures(instrument: str, workdir: Path) -> list[tuple[str, Path, list]]:
    from test_fixtures.benchmark_suite import get_benchmark_suite
    from test_fixtures.reference import generate_reference_clip

    prepared = []
    for spec in get_benchmark_suite():
        if spec.instrument != instrument:
            continue
        wav_path = workdir / f"{spec.name}.wav"
        clip = generate_reference_clip(spec, wav_path)
        norm_path = workdir / f"{spec.name}_norm.wav"
        _normalize(clip.path, norm_path)
        prepared.append((spec.name, norm_path, clip.events))
    return prepared


def run_sweep(
    instrument: str,
    onset_thresholds: list[float],
    frame_thresholds: list[float],
    workdir: Path,
) -> list[SweepResult]:
    from basic_pitch import ICASSP_2022_MODEL_PATH
    from basic_pitch.inference import predict

    from aura_worker.eval import metrics
    from aura_worker.ghost_filter import filter_ghost_notes
    from score_schema.models import NoteEvent

    fixtures = _prepare_fixtures(instrument, workdir)
    if not fixtures:
        raise ValueError(f"no benchmark_suite fixtures for instrument={instrument!r}")

    results = []
    for onset_t in onset_thresholds:
        for frame_t in frame_thresholds:
            per_fixture_f1: dict[str, float] = {}
            per_fixture_raw_f1: dict[str, float] = {}
            for name, norm_path, reference_events in fixtures:
                _, _, note_events = predict(
                    str(norm_path),
                    model_or_model_path=ICASSP_2022_MODEL_PATH,
                    onset_threshold=onset_t,
                    frame_threshold=frame_t,
                )
                raw_notes = [
                    NoteEvent(
                        pitch=int(round(pitch_midi)),
                        onset_s=float(start_s),
                        offset_s=float(end_s),
                        velocity=int(round(min(max(amplitude, 0.0), 1.0) * 127)),
                        confidence=float(min(max(amplitude, 0.0), 1.0)),
                    )
                    for start_s, end_s, pitch_midi, amplitude, *_rest in note_events
                ]
                filtered_notes = filter_ghost_notes(raw_notes)
                per_fixture_raw_f1[name] = metrics.onset_f1(reference_events, raw_notes).f1
                per_fixture_f1[name] = metrics.onset_f1(reference_events, filtered_notes).f1
            results.append(
                SweepResult(
                    onset_threshold=onset_t,
                    frame_threshold=frame_t,
                    per_fixture_f1=per_fixture_f1,
                    per_fixture_raw_f1=per_fixture_raw_f1,
                )
            )
    return results


def _render_markdown(instrument: str, results: list[SweepResult]) -> str:
    lines = [f"### {instrument}: onset/frame threshold grid (post-ghost-filter onset F1)", ""]
    fixture_names = sorted(results[0].per_fixture_f1.keys())
    header = ["onset", "frame", "mean_f1", "min_f1"] + [n.split("_", 1)[1][:12] for n in fixture_names]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for r in results:
        row = [f"{r.onset_threshold}", f"{r.frame_threshold}", f"{r.mean_f1:.3f}", f"{r.min_f1:.3f}"]
        row += [f"{r.per_fixture_f1[n]:.2f}" for n in fixture_names]
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="write Markdown output here (also printed)")
    args = parser.parse_args(argv)

    # Coarse grid first (onset), then a fine grid around each instrument's
    # best onset value (frame) -- this two-stage shape mirrors exactly how
    # the values in aura_worker.instrument_thresholds were originally found,
    # so this script reproduces that derivation, not just today's optimum.
    onset_grid = [0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95]
    frame_grid_guitar = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    frame_grid_piano_coarse = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6]
    frame_grid_piano_fine = [0.05, 0.08, 0.1, 0.12, 0.15]

    sections: list[str] = [
        "# Per-instrument basic-pitch threshold grid search — evidence",
        "",
        "Rerunnable evidence for `aura_worker.instrument_thresholds`'s tuned",
        "`onset_threshold`/`frame_threshold` values. Reproduce with:",
        "",
        "```",
        "uv run --package aura-worker python "
        "workers/transcription/scripts/tune_instrument_thresholds.py",
        "```",
        "",
        "See that script's module docstring for the full methodology and the",
        "held-out-set caveat (there isn't one -- see",
        "docs/superpowers/SESSION-HANDOFF.md's item 1 entry).",
        "",
    ]

    with tempfile.TemporaryDirectory(prefix="aura_threshold_sweep_") as tmp:
        workdir = Path(tmp)

        print("Sweeping guitar onset_threshold (frame=0.3)...")
        guitar_onset = run_sweep("guitar", onset_grid, [0.3], workdir)
        sections.append(_render_markdown("guitar (onset sweep, frame=0.3)", guitar_onset))

        print("Sweeping guitar frame_threshold (onset=0.8)...")
        guitar_frame = run_sweep("guitar", [0.8], frame_grid_guitar, workdir)
        sections.append(_render_markdown("guitar (frame sweep, onset=0.8)", guitar_frame))

        print("Sweeping piano onset_threshold (frame=0.3)...")
        piano_onset = run_sweep("piano", onset_grid, [0.3], workdir)
        sections.append(_render_markdown("piano (onset sweep, frame=0.3)", piano_onset))

        print("Sweeping piano frame_threshold, coarse (onset=0.8)...")
        piano_frame_coarse = run_sweep("piano", [0.8], frame_grid_piano_coarse, workdir)
        sections.append(_render_markdown("piano (frame sweep, coarse, onset=0.8)", piano_frame_coarse))

        print("Sweeping piano frame_threshold, fine around 0.1 (onset=0.8)...")
        piano_frame_fine = run_sweep("piano", [0.8], frame_grid_piano_fine, workdir)
        sections.append(
            _render_markdown("piano (frame sweep, fine around 0.1, onset=0.8)", piano_frame_fine)
        )

    markdown = "\n".join(sections)
    print(markdown)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown)
        print(f"\nWrote {args.out}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
