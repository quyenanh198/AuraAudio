import json

from aura_worker.eval.metrics import NoteF1Result
from aura_worker.eval.report import (
    BenchmarkReport,
    FixtureResult,
    build_json_report,
    build_markdown_report,
    compute_aggregate,
)


def _good_result(name: str) -> FixtureResult:
    return FixtureResult(
        name=name,
        source="synthetic",
        instrument="guitar",
        truth_tempo_bpm=120.0,
        truth_meter="4/4",
        truth_key="C major",
        detected_tempo_bpm=121.0,
        detected_meter="4/4",
        detected_key="C major",
        tempo_ok=True,
        meter_ok=True,
        key_ok=True,
        onset_f1=NoteF1Result(precision=0.9, recall=0.8, f1=0.85),
        onset_offset_f1=NoteF1Result(precision=0.7, recall=0.6, f1=0.65),
    )


def _bad_result(name: str) -> FixtureResult:
    return FixtureResult(
        name=name,
        source="synthetic",
        instrument="piano",
        truth_tempo_bpm=100.0,
        truth_meter="3/4",
        truth_key="D minor",
        detected_tempo_bpm=140.0,
        detected_meter="4/4",
        detected_key="C major",
        tempo_ok=False,
        meter_ok=False,
        key_ok=False,
        onset_f1=NoteF1Result(precision=0.1, recall=0.1, f1=0.1),
        onset_offset_f1=NoteF1Result(precision=0.0, recall=0.0, f1=0.0),
    )


def _error_result(name: str) -> FixtureResult:
    return FixtureResult(
        name=name,
        source="synthetic",
        instrument="guitar",
        truth_tempo_bpm=90.0,
        truth_meter="4/4",
        truth_key="G major",
        error="JobFailure: NO_MUSIC_DETECTED",
    )


def test_compute_aggregate_averages_only_scored_fixtures():
    results = [_good_result("a"), _bad_result("b"), _error_result("c")]

    agg = compute_aggregate(results)

    assert agg.fixture_count == 3
    assert agg.scored_count == 2
    assert agg.error_count == 1
    assert abs(agg.mean_onset_f1 - (0.85 + 0.1) / 2) < 1e-9
    assert abs(agg.tempo_accuracy - 0.5) < 1e-9  # 1 of 2 scored fixtures matched
    assert abs(agg.key_accuracy - 0.5) < 1e-9
    assert abs(agg.meter_accuracy - 0.5) < 1e-9


def test_compute_aggregate_handles_empty_list():
    agg = compute_aggregate([])
    assert agg.fixture_count == 0
    assert agg.mean_onset_f1 == 0.0


def test_compute_aggregate_all_errored_gives_zeroed_metrics_not_crash():
    agg = compute_aggregate([_error_result("a"), _error_result("b")])
    assert agg.scored_count == 0
    assert agg.error_count == 2
    assert agg.mean_onset_f1 == 0.0


def test_build_markdown_report_includes_commit_and_suite_version():
    results = [_good_result("a")]
    report = BenchmarkReport(
        generated_at="2026-08-21T00:00:00Z",
        commit="8cbc350",
        suite_version="2026-08-21-v1",
        fixture_results=results,
        aggregate=compute_aggregate(results),
    )

    md = build_markdown_report(report)

    assert "8cbc350" in md
    assert "2026-08-21-v1" in md
    assert "a" in md
    assert "no `--manifest`" in md.lower() or "none (no" in md.lower()


def test_build_markdown_report_shows_manifest_path_when_present():
    report = BenchmarkReport(
        generated_at="x",
        commit="x",
        suite_version="x",
        fixture_results=[],
        aggregate=compute_aggregate([]),
        manifest_path="/home/user/my_manifest.json",
    )
    md = build_markdown_report(report)
    assert "/home/user/my_manifest.json" in md


def test_build_markdown_report_shows_error_rows_without_crashing():
    results = [_error_result("broken")]
    report = BenchmarkReport(
        generated_at="x", commit="x", suite_version="x",
        fixture_results=results, aggregate=compute_aggregate(results),
    )
    md = build_markdown_report(report)
    assert "broken" in md
    assert "ERROR" in md
    assert "NO_MUSIC_DETECTED" in md


def test_build_json_report_round_trips_through_json_dumps():
    results = [_good_result("a"), _error_result("b")]
    report = BenchmarkReport(
        generated_at="2026-08-21T00:00:00Z",
        commit="8cbc350",
        suite_version="2026-08-21-v1",
        fixture_results=results,
        aggregate=compute_aggregate(results),
    )

    payload = build_json_report(report)
    text = json.dumps(payload)  # must not raise
    reloaded = json.loads(text)

    assert reloaded["commit"] == "8cbc350"
    assert reloaded["suite_version"] == "2026-08-21-v1"
    assert reloaded["aggregate"]["scored_count"] == 1
    assert len(reloaded["fixtures"]) == 2
    assert reloaded["fixtures"][0]["onset_f1"]["f1"] == 0.85
    assert reloaded["fixtures"][1]["error"] == "JobFailure: NO_MUSIC_DETECTED"
