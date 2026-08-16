from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import JobErrorCode, NoteEvent

STAGE_VERSION = 1
METER_CANDIDATES = {"4/4": 4, "3/4": 3}
ACCENT_HALF_WINDOW_S = 0.05


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


def _detect_meter(y, sr, beat_times: np.ndarray) -> tuple[str, float]:
    import librosa

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_sr = sr / 512.0
    accents = np.array([_accent_at(onset_env, onset_sr, t) for t in beat_times])
    overall_mean = float(np.mean(accents)) if len(accents) else 0.0

    margins: dict[str, float] = {}
    for meter_name, group in METER_CANDIDATES.items():
        offset_scores = [
            float(np.mean(accents[offset::group]))
            for offset in range(group)
            if len(accents[offset::group]) >= 1
        ]
        margins[meter_name] = (max(offset_scores) - overall_mean) if offset_scores else 0.0

    best_meter = max(margins, key=margins.get)
    total_margin = sum(max(m, 0.0) for m in margins.values())
    confidence = (max(margins[best_meter], 0.0) / total_margin) if total_margin > 0 else 0.5
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
