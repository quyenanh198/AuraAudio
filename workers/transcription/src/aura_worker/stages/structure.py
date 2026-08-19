from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.meters import DETECTABLE_METERS, is_compound, notated_beats
from score_schema.models import JobErrorCode, NoteEvent

STAGE_VERSION = 2
ACCENT_HALF_WINDOW_S = 0.05
SECONDARY_ACCENT_WEIGHT = 0.5


@dataclass
class StructureResult:
    tempo_bpm: float
    meter: str
    key: str
    tempo_confidence: float
    meter_confidence: float
    key_confidence: float


def _detect_tempo_and_beats(y, sr):
    import librosa

    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    tempo_bpm = float(np.atleast_1d(tempo)[0])
    return tempo_bpm, beat_times


def _tempo_confidence(beat_times: np.ndarray) -> float:
    if len(beat_times) < 3:
        return 0.0
    intervals = np.diff(beat_times)
    mean_interval = float(np.mean(intervals))
    if mean_interval <= 0:
        return 0.0
    stddev_ratio = float(np.std(intervals)) / mean_interval
    return float(np.clip(1.0 - stddev_ratio, 0.0, 1.0))


def _accent_at(onset_env: np.ndarray, onset_sr: float, t: float, half_window: float = ACCENT_HALF_WINDOW_S) -> float:
    i0 = int(round((t - half_window) * onset_sr))
    i1 = int(round((t + half_window) * onset_sr))
    i0 = max(i0, 0)
    i1 = min(i1, len(onset_env))
    if i1 <= i0:
        return 0.0
    return float(np.max(onset_env[i0:i1]))


def _comb_score(accents: np.ndarray, period: int, offset: int) -> float:
    comb = accents[offset::period]
    return float(np.mean(comb)) if len(comb) >= 1 else 0.0


def _detect_meter(y, sr, beat_times: np.ndarray) -> tuple[str, float]:
    import librosa

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_sr = sr / 512.0
    accents = np.array([_accent_at(onset_env, onset_sr, t) for t in beat_times])
    overall_mean = float(np.mean(accents)) if len(accents) else 0.0

    # Two complementary margins per candidate, blended by rank (below) rather
    # than by a single formula:
    #
    # - mean_margins: winning offset's comb score minus the overall accent
    #   mean. Robust when a meter has a genuine secondary accent (real 4/4's
    #   beat 3, real 6/8's secondary eighth) — the classic approach.
    # - peak_margins: winning offset's comb score minus its OWN runner-up
    #   offset. This is what catches subharmonic aliasing: true 2/4 data
    #   scores identically at every even offset of a 4/4-period comb (2
    #   divides 4), so 4/4's peak_margin collapses toward zero and 4/4 stops
    #   looking like a competitive candidate, correctly leaving 2/4 (whose
    #   own comb has a real winner/runner-up gap) on top. The same relation
    #   holds for 3/4 vs. 6/8 (3 divides 6).
    #
    # mean_margins alone cannot tell true 2/4 from 4/4-shaped-like-2/4 (both
    # score identically); peak_margins alone is fooled by a real secondary
    # accent (a non-trivial runner-up looks like "no clear winner" even
    # though it is exactly the signature of the longer meter). Combining
    # both by rank (Borda count) is what survives both fixture families.
    mean_margins: dict[str, float] = {}
    peak_margins: dict[str, float] = {}
    for meter_name in DETECTABLE_METERS:
        if is_compound(meter_name):
            period = int(meter_name.split("/")[0])  # 6 tracked eighths for 6/8
            offset_scores = []
            best = 0.0
            for offset in range(period):
                primary = _comb_score(accents, period, offset)
                secondary = _comb_score(accents, period, (offset + period // 2) % period)
                blended = primary + SECONDARY_ACCENT_WEIGHT * secondary
                offset_scores.append(blended)
                best = max(best, blended)
            mean_margins[meter_name] = best - (1.0 + SECONDARY_ACCENT_WEIGHT) * overall_mean
        else:
            period = notated_beats(meter_name)
            offset_scores = [_comb_score(accents, period, o) for o in range(period)]
            mean_margins[meter_name] = (max(offset_scores) - overall_mean) if offset_scores else 0.0

        ranked = sorted(offset_scores, reverse=True)
        peak_margins[meter_name] = ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)

    by_mean = sorted(DETECTABLE_METERS, key=lambda m: -mean_margins[m])
    by_peak = sorted(DETECTABLE_METERS, key=lambda m: -peak_margins[m])
    rank_mean = {m: i for i, m in enumerate(by_mean)}
    rank_peak = {m: i for i, m in enumerate(by_peak)}
    combined_rank = {m: rank_mean[m] + rank_peak[m] for m in DETECTABLE_METERS}
    # combined_rank ties happen routinely for 6/8 vs. 3/4: a real secondary
    # accent makes mean_margin favor 6/8 decisively while the SAME accent
    # makes peak_margin favor 3/4 decisively (see the module-level note
    # above), and this holds whether the underlying clip truly is 6/8 or is
    # 3/4 data whose period-3 pattern aliases into the period-6 comb (3
    # divides 6). We investigated breaking these ties by mean_margin
    # magnitude (largest wins): it does resolve a hand-built noiseless 6/8
    # accent pattern correctly, but it also flips the pre-existing 3/4
    # regression fixture (write_metronome_pulse_wav) to 6/8 — its
    # mean_margin ratio between the two candidates (~1.81x) is not
    # reliably smaller than a genuine 6/8 clip's ratio (~1.75x in the same
    # hand-built pattern), so magnitude alone cannot separate "real 6/8"
    # from "3/4 aliased as 6/8" with this scoring signal. Given that, ties
    # fall back to DETECTABLE_METERS's declared order (4/4, 3/4, 6/8, 2/4),
    # which conservatively prefers the simpler/shorter meter whenever the
    # two margins disagree — see test_detects_6_8_across_validated_tempos
    # for the resulting (real, not universal) set of tempi at which a
    # genuinely 6/8 clip wins outright rather than tying.
    best_meter = min(DETECTABLE_METERS, key=lambda m: (combined_rank[m], DETECTABLE_METERS.index(m)))

    total_margin = sum(max(m, 0.0) for m in mean_margins.values())
    confidence = (max(mean_margins[best_meter], 0.0) / total_margin) if total_margin > 0 else 0.5
    return best_meter, float(np.clip(confidence, 0.0, 1.0))


def _detect_key(notes: list[NoteEvent]) -> tuple[str, float]:
    from music21 import note as m21_note
    from music21 import stream

    s = stream.Stream()
    for n in notes:
        s.append(m21_note.Note(n.pitch))
    analyzed = s.analyze("krumhansl")
    key_str = f"{analyzed.tonic.name} {analyzed.mode}"
    confidence = float(np.clip(analyzed.correlationCoefficient, 0.0, 1.0))
    return key_str, confidence


def run(ctx: StageContext, normalized_path: Path, notes: list[NoteEvent]) -> StructureResult:
    cached = find_cached_artifact(ctx, "structure", STAGE_VERSION)
    if cached is not None:
        raw = json.loads(ctx.storage.get_bytes(cached.object_key))
        return StructureResult(**raw)

    import librosa

    y, sr = librosa.load(str(normalized_path), sr=None)
    tempo_bpm, beat_times = _detect_tempo_and_beats(y, sr)

    if len(beat_times) < 2:
        raise JobFailure(JobErrorCode.MODEL_FAILED, "beat tracking found fewer than 2 beats")

    tempo_confidence = _tempo_confidence(beat_times)
    meter, meter_confidence = _detect_meter(y, sr, beat_times)
    key, key_confidence = _detect_key(notes)

    result = StructureResult(
        tempo_bpm=tempo_bpm, meter=meter, key=key,
        tempo_confidence=tempo_confidence, meter_confidence=meter_confidence, key_confidence=key_confidence,
    )

    object_key = f"jobs/{ctx.job.id}/stage/structure.json"
    payload = json.dumps(result.__dict__).encode()
    ctx.storage.put_bytes(object_key, payload)
    save_artifact(
        ctx, "structure", STAGE_VERSION, object_key=object_key,
        sha256=hashlib.sha256(payload).hexdigest(),
        metrics={"tempo_bpm": tempo_bpm, "meter": result.meter, "key": result.key},
    )
    return result
