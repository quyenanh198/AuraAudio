"""Pure scoring functions for the transcription benchmark harness — no I/O,
no pipeline invocation, just reference-vs-estimated comparisons. Kept
separate from pipeline.py/benchmark.py so these are trivially unit-testable
(see workers/transcription/tests/test_eval_metrics.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np


class _TimedPitchedEvent(Protocol):
    """Structural shape shared by score_schema.models.NoteEvent (predicted
    notes) and test_fixtures.reference.ReferenceEvent (ground truth) — the
    two event types this module scores against each other."""

    pitch: int
    onset_s: float
    offset_s: float


def midi_to_hz(midi_pitch: float) -> float:
    """mir_eval.transcription compares pitches in Hertz (not MIDI/cents),
    per its own documented convention."""
    return 440.0 * (2.0 ** ((midi_pitch - 69) / 12.0))


def _to_mir_eval_arrays(events: list[_TimedPitchedEvent]) -> tuple[np.ndarray, np.ndarray]:
    if not events:
        return np.zeros((0, 2)), np.zeros(0)
    intervals = np.array([[e.onset_s, e.offset_s] for e in events], dtype=float)
    pitches_hz = np.array([midi_to_hz(e.pitch) for e in events], dtype=float)
    return intervals, pitches_hz


@dataclass(frozen=True)
class NoteF1Result:
    precision: float
    recall: float
    f1: float


def onset_f1(
    reference_events: list[_TimedPitchedEvent],
    estimated_events: list[_TimedPitchedEvent],
    onset_tolerance_s: float = 0.05,
    pitch_tolerance_cents: float = 50.0,
) -> NoteF1Result:
    """Note onset F1: a predicted note counts as correct if its onset is
    within `onset_tolerance_s` of a reference note's onset and its pitch is
    within `pitch_tolerance_cents` (default 50 cents = a quarter tone) —
    offsets are ignored. Delegates to mir_eval.transcription, the standard
    library for this exact metric (see mir_eval.transcription.precision_recall_f1_overlap's
    own docstring for the underlying bipartite-matching algorithm)."""
    import mir_eval.transcription as mir_transcription

    ref_intervals, ref_pitches = _to_mir_eval_arrays(reference_events)
    est_intervals, est_pitches = _to_mir_eval_arrays(estimated_events)
    if len(ref_pitches) == 0 or len(est_pitches) == 0:
        return NoteF1Result(precision=0.0, recall=0.0, f1=0.0)

    # precision_recall_f1_overlap returns a 4-tuple (precision, recall,
    # f_measure, average_overlap_ratio) -- the overlap ratio is only
    # meaningful when offsets are scored (offset_ratio is not None), so it's
    # discarded here.
    precision, recall, f1, _average_overlap_ratio = mir_transcription.precision_recall_f1_overlap(
        ref_intervals,
        ref_pitches,
        est_intervals,
        est_pitches,
        onset_tolerance=onset_tolerance_s,
        pitch_tolerance=pitch_tolerance_cents,
        offset_ratio=None,  # ignore offsets entirely for the onset-only metric
    )
    return NoteF1Result(precision=float(precision), recall=float(recall), f1=float(f1))


def onset_offset_f1(
    reference_events: list[_TimedPitchedEvent],
    estimated_events: list[_TimedPitchedEvent],
    onset_tolerance_s: float = 0.05,
    pitch_tolerance_cents: float = 50.0,
    offset_ratio: float = 0.2,
    offset_min_tolerance_s: float = 0.05,
) -> NoteF1Result:
    """Note onset+offset F1: on top of onset_f1's onset/pitch requirements,
    a correct note's offset must also fall within `offset_ratio` of the
    reference note's duration around the reference offset (or
    `offset_min_tolerance_s`, whichever is larger) — mir_eval's standard
    "strict" transcription metric."""
    import mir_eval.transcription as mir_transcription

    ref_intervals, ref_pitches = _to_mir_eval_arrays(reference_events)
    est_intervals, est_pitches = _to_mir_eval_arrays(estimated_events)
    if len(ref_pitches) == 0 or len(est_pitches) == 0:
        return NoteF1Result(precision=0.0, recall=0.0, f1=0.0)

    precision, recall, f1, _average_overlap_ratio = mir_transcription.precision_recall_f1_overlap(
        ref_intervals,
        ref_pitches,
        est_intervals,
        est_pitches,
        onset_tolerance=onset_tolerance_s,
        pitch_tolerance=pitch_tolerance_cents,
        offset_ratio=offset_ratio,
        offset_min_tolerance=offset_min_tolerance_s,
    )
    return NoteF1Result(precision=float(precision), recall=float(recall), f1=float(f1))


def tempo_within_tolerance(detected_bpm: float, truth_bpm: float, rel_tol: float = 0.05) -> bool:
    """True if `detected_bpm` is within `rel_tol` (default ±5%) of `truth_bpm`."""
    if truth_bpm <= 0:
        return False
    return abs(detected_bpm - truth_bpm) / truth_bpm <= rel_tol


def key_matches(detected_key: str, truth_key: str) -> bool:
    """Case-insensitive exact match (e.g. "C major" == "c major")."""
    return detected_key.strip().lower() == truth_key.strip().lower()


def meter_matches(detected_meter: str, truth_meter: str) -> bool:
    """Exact string match (meters are canonical "N/D" strings — see
    score_schema.meters.SUPPORTED_METERS)."""
    return detected_meter.strip() == truth_meter.strip()
