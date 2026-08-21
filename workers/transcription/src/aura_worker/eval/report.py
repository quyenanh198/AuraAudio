"""Aggregation + Markdown/JSON rendering for one benchmark run. Kept
separate from benchmark.py's CLI/orchestration so the report shape is
unit-testable without running the real pipeline (see
workers/transcription/tests/test_eval_report.py)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from aura_worker.eval.metrics import NoteF1Result


@dataclass(frozen=True)
class FixtureResult:
    name: str
    source: str  # "synthetic" | "manifest"
    instrument: str
    truth_tempo_bpm: float | None
    truth_meter: str | None
    truth_key: str | None
    detected_tempo_bpm: float | None = None
    detected_meter: str | None = None
    detected_key: str | None = None
    tempo_ok: bool | None = None
    meter_ok: bool | None = None
    key_ok: bool | None = None
    onset_f1: NoteF1Result | None = None
    onset_offset_f1: NoteF1Result | None = None
    error: str | None = None  # set (only) if the pipeline raised for this fixture


@dataclass(frozen=True)
class AggregateMetrics:
    fixture_count: int
    scored_count: int  # excludes fixtures that errored
    error_count: int
    mean_onset_f1: float
    mean_onset_offset_f1: float
    tempo_accuracy: float  # fraction of scored fixtures within tolerance
    key_accuracy: float
    meter_accuracy: float


@dataclass(frozen=True)
class BenchmarkReport:
    generated_at: str  # ISO 8601
    commit: str
    suite_version: str
    fixture_results: list[FixtureResult] = field(default_factory=list)
    aggregate: AggregateMetrics | None = None
    manifest_path: str | None = None


def compute_aggregate(fixture_results: list[FixtureResult]) -> AggregateMetrics:
    scored = [r for r in fixture_results if r.error is None]
    error_count = len(fixture_results) - len(scored)

    def _mean(values: list[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    return AggregateMetrics(
        fixture_count=len(fixture_results),
        scored_count=len(scored),
        error_count=error_count,
        mean_onset_f1=_mean([r.onset_f1.f1 for r in scored if r.onset_f1 is not None]),
        mean_onset_offset_f1=_mean(
            [r.onset_offset_f1.f1 for r in scored if r.onset_offset_f1 is not None]
        ),
        tempo_accuracy=_mean(
            [1.0 if r.tempo_ok else 0.0 for r in scored if r.tempo_ok is not None]
        ),
        key_accuracy=_mean([1.0 if r.key_ok else 0.0 for r in scored if r.key_ok is not None]),
        meter_accuracy=_mean(
            [1.0 if r.meter_ok else 0.0 for r in scored if r.meter_ok is not None]
        ),
    )


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _fmt_f1(x: float) -> str:
    return f"{x:.3f}"


def build_markdown_report(report: BenchmarkReport) -> str:
    lines: list[str] = []
    lines.append("# Transcription benchmark report")
    lines.append("")
    lines.append(
        "Detection-quality benchmark harness "
        "(docs/superpowers/SESSION-HANDOFF.md's \"Detection-quality roadmap\", item 0). "
        "Measurement only — no pipeline behavior was changed to produce this report."
    )
    lines.append("")
    lines.append(f"- **Generated:** {report.generated_at}")
    lines.append(f"- **Commit:** `{report.commit}`")
    lines.append(f"- **Fixture-suite version:** `{report.suite_version}`")
    if report.manifest_path:
        lines.append(f"- **External manifest:** `{report.manifest_path}`")
    else:
        lines.append("- **External manifest:** none (no `--manifest` passed)")
    lines.append("")

    lines.append("## Manifest format")
    lines.append("")
    lines.append(
        "An optional `--manifest <path.json>` lists local real recordings + reference "
        "MIDI for scoring alongside the synthetic suite. Gitignored, absent by default. "
        "Format (see `aura_worker.eval.manifest.load_manifest`):"
    )
    lines.append("")
    lines.append("```json")
    lines.append(
        '{"entries": [{"name": "...", "audio_path": "...", '
        '"reference_midi_path": "...", "instrument": "guitar"}]}'
    )
    lines.append("```")
    lines.append("")

    agg = report.aggregate
    lines.append("## Aggregate")
    lines.append("")
    if agg is not None:
        lines.append("| Metric | Value |")
        lines.append("|---|---|")
        lines.append(
            f"| Fixtures | {agg.fixture_count} "
            f"({agg.scored_count} scored, {agg.error_count} errored) |"
        )
        lines.append(f"| Mean onset F1 (50ms) | {_fmt_f1(agg.mean_onset_f1)} |")
        lines.append(f"| Mean onset+offset F1 | {_fmt_f1(agg.mean_onset_offset_f1)} |")
        lines.append(f"| Tempo accuracy (±5%) | {_fmt_pct(agg.tempo_accuracy)} |")
        lines.append(f"| Key accuracy (exact) | {_fmt_pct(agg.key_accuracy)} |")
        lines.append(f"| Meter accuracy (exact) | {_fmt_pct(agg.meter_accuracy)} |")
    else:
        lines.append("_No fixtures scored._")
    lines.append("")

    lines.append("## Per-fixture")
    lines.append("")
    lines.append(
        "| Fixture | Source | Instr. | Onset F1 | Onset+Offset F1 "
        "| Tempo (truth→detected) | Meter (truth→detected) | Key (truth→detected) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in report.fixture_results:
        if r.error is not None:
            lines.append(
                f"| {r.name} | {r.source} | {r.instrument} | ERROR | ERROR | — | — | {r.error} |"
            )
            continue
        onset_f1_str = _fmt_f1(r.onset_f1.f1) if r.onset_f1 else "—"
        onset_off_f1_str = _fmt_f1(r.onset_offset_f1.f1) if r.onset_offset_f1 else "—"
        tempo_str = (
            f"{r.truth_tempo_bpm:.1f}→{r.detected_tempo_bpm:.1f} {'OK' if r.tempo_ok else 'MISS'}"
            if r.truth_tempo_bpm is not None and r.detected_tempo_bpm is not None
            else "—"
        )
        meter_str = (
            f"{r.truth_meter}→{r.detected_meter} {'OK' if r.meter_ok else 'MISS'}"
            if r.truth_meter is not None
            else "—"
        )
        key_str = (
            f"{r.truth_key}→{r.detected_key} {'OK' if r.key_ok else 'MISS'}"
            if r.truth_key is not None
            else "—"
        )
        lines.append(
            f"| {r.name} | {r.source} | {r.instrument} | {onset_f1_str} | {onset_off_f1_str} "
            f"| {tempo_str} | {meter_str} | {key_str} |"
        )
    lines.append("")

    return "\n".join(lines)


def build_json_report(report: BenchmarkReport) -> dict:
    def _f1_to_dict(f1: NoteF1Result | None) -> dict | None:
        return asdict(f1) if f1 is not None else None

    return {
        "generated_at": report.generated_at,
        "commit": report.commit,
        "suite_version": report.suite_version,
        "manifest_path": report.manifest_path,
        "aggregate": asdict(report.aggregate) if report.aggregate is not None else None,
        "fixtures": [
            {
                **{k: v for k, v in asdict(r).items() if k not in ("onset_f1", "onset_offset_f1")},
                "onset_f1": _f1_to_dict(r.onset_f1),
                "onset_offset_f1": _f1_to_dict(r.onset_offset_f1),
            }
            for r in report.fixture_results
        ],
    }
