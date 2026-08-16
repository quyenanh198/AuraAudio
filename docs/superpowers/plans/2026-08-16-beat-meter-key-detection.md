# Beat, Meter, and Key Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the transcription pipeline's hardcoded 120 BPM / 4/4 quantization grid with real per-clip detection of tempo, meter (4/4 or 3/4), and major/minor key, and thread the detected values through quantization, MIDI, and MusicXML export.

**Architecture:** A new worker stage, `structure`, runs between `inference` and `quantize`. It detects tempo and beat times via `librosa.beat.beat_track`, scores meter candidates with a validated accent-periodicity technique on `librosa.onset.onset_strength`, and detects key via `music21`'s built-in Krumhansl-Schmuckler analysis on the transcribed note pitches. The canonical score schema bumps to v2 to carry these values plus confidence scores; `quantize` and `musicxml/export.py` consume them instead of hardcoded constants.

**Tech Stack:** `librosa` (already transitively installed via `basic-pitch`), `music21` (already a direct dependency of `packages/musicxml`) — no new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-15-beat-meter-key-detection-design.md` — this plan implements it as amended (meter scope narrowed to `{4/4, 3/4}` after empirical prototyping; see the spec's Non-Goals section for why 6/8 and 2/4 were dropped).

## Global Constraints

(Copied from the spec, which itself inherits ARCHITECTURE.md's constraints via the Phase 1 plan.)

- One global tempo per clip, not a time-varying curve.
- Meter candidates are exactly `{"4/4", "3/4"}` — nothing else.
- Single voice + chords only; no true multi-voice notation.
- Quantization grid stays a straight 16th note (no triplets/tuplets).
- No new `JobErrorCode` values; the one new failure path reuses `MODEL_FAILED`.
- No correction UI exists yet — low confidence is stored, never blocks the job.
- `schemaVersion` bumps `1` → `2` as an accepted breaking change; no migration tooling (no production data exists yet).
- Key names use `music21`'s native accidental notation — a flat is written `"-"` (e.g. `"B- major"`), **not** `"b"` — because `analyzed.tonic.name` produces `"-"` and the value must round-trip directly into `music21.key.Key(tonic, mode)` without a translation layer.
- `tempo.MetronomeMark` must be inserted into the first `Measure`, not the `Part` — empirically confirmed: a `MetronomeMark` inserted at the `Part` level is silently dropped from the exported MusicXML, while `TimeSignature` and `Key` inserted at the `Part` level both propagate correctly into the measure's `<attributes>`. This asymmetry was verified directly against `music21` 10.5.0's actual output, not assumed.

## File Structure

```text
packages/score_schema/src/score_schema/
  models.py       # Modify: build_score() gains tempo_bpm/meter/key/confidence params, schemaVersion -> 2
  validate.py      # Modify: v2 JSON Schema (tempoBpm/meter/key/confidence required per part)
packages/score_schema/tests/
  test_models.py    # Modify: build_score() call sites updated
  test_validate.py   # Modify: v2 fixtures, v1-rejection test

packages/test_fixtures/src/test_fixtures/
  generate.py      # Modify: add write_metronome_pulse_wav(), write_diatonic_melody_wav()
packages/test_fixtures/tests/
  test_generate.py   # Modify: coverage for the two new generators

workers/transcription/src/aura_worker/
  stage_runner.py    # Modify: STAGE_PROGRESS gains "structure": 65
  stages/
    structure.py     # Create: tempo/meter/key detection stage
    quantize.py      # Modify: consumes StructureResult instead of hardcoded BPM/meter
    export.py        # Modify: MIDI tempo reads score's tempoBpm instead of hardcoded 120
  runner.py         # Modify: wire structure.run into the pipeline
workers/transcription/tests/
  test_structure.py   # Create
  test_quantize.py    # Modify
  test_export.py     # Modify (score fixture now needs v2 fields)

packages/musicxml/src/musicxml/
  export.py        # Modify: reads meter/tempoBpm/key from score; key-aware enharmonic spelling
packages/musicxml/tests/
  test_export.py     # Modify: v2 score fixture; new assertions for time signature/tempo/key
  test_validate.py    # Modify: v2 score fixture

apps/api/tests/
  test_e2e_pipeline.py # Unchanged in structure — verified in Task 7 that it still passes as-is
```

Responsibilities are unchanged from the Phase 1 file structure — `structure.py` is a new, single-purpose stage module following the exact pattern every other stage (`probe`, `normalize`, `inference`, `quantize`) already uses: a `run(ctx, ...)` function, cache-check via `find_cached_artifact`, persist via `save_artifact`.

---

## Task 1: `score_schema` v2 — tempo/meter/key fields

**Files:**
- Modify: `packages/score_schema/src/score_schema/models.py`
- Modify: `packages/score_schema/src/score_schema/validate.py`
- Modify: `packages/score_schema/tests/test_models.py`
- Modify: `packages/score_schema/tests/test_validate.py`

**Interfaces:**
- Produces: `build_score(instrument, tempo_bpm, meter, key, confidence, time_map, measures) -> dict` (schemaVersion 2). `validate_score` accepts only schemaVersion 2 and requires `tempoBpm`/`meter`/`key`/`confidence` on every part.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing test for the new `build_score` signature**

Replace the body of `packages/score_schema/tests/test_models.py`'s `test_build_score_produces_schema_v1_shape` with:

```python
def test_build_score_produces_schema_v2_shape():
    score = build_score(
        instrument="guitar",
        tempo_bpm=128.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_01",
                        "pitch": 64,
                        "onsetSeconds": 0.61,
                        "offsetSeconds": 1.08,
                        "notatedOnset": "1/4",
                        "notatedDuration": "1/4",
                        "voice": 1,
                        "confidence": 0.91,
                        "locked": False,
                    }
                ],
            }
        ],
    )
    assert score["schemaVersion"] == 2
    part = score["parts"][0]
    assert part["instrument"] == "guitar"
    assert part["tempoBpm"] == 128.0
    assert part["meter"] == "4/4"
    assert part["key"] == "C major"
    assert part["confidence"] == {"tempo": 0.9, "meter": 0.8, "key": 0.7}
    assert part["measures"][0]["events"][0]["pitch"] == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_models.py -v`
Expected: FAIL — `build_score() got an unexpected keyword argument 'tempo_bpm'`

- [ ] **Step 3: Update `build_score`**

```python
# packages/score_schema/src/score_schema/models.py — replace build_score()
def build_score(
    instrument: str,
    tempo_bpm: float,
    meter: str,
    key: str,
    confidence: dict,
    time_map: list[dict],
    measures: list[dict],
) -> dict:
    """Assemble the canonical schemaVersion-2 score JSON (ARCHITECTURE.md §5,
    extended per docs/superpowers/specs/2026-08-15-beat-meter-key-detection-design.md)."""
    return {
        "schemaVersion": 2,
        "timeMap": time_map,
        "parts": [
            {
                "instrument": instrument,
                "tempoBpm": tempo_bpm,
                "meter": meter,
                "key": key,
                "confidence": confidence,
                "measures": measures,
            }
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_models.py -v`
Expected: PASS (3 tests — the other two, `test_note_event_is_immutable_and_typed` and `test_job_error_code_values_match_spec`, are unaffected)

- [ ] **Step 5: Write the failing test for v2 validation**

Replace `packages/score_schema/tests/test_validate.py` in full:

```python
import pytest

from score_schema.models import build_score
from score_schema.validate import ScoreValidationError, validate_score


def _valid_score():
    return build_score(
        instrument="piano",
        tempo_bpm=100.0,
        meter="3/4",
        key="A minor",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.6},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_01",
                        "pitch": 60,
                        "onsetSeconds": 0.0,
                        "offsetSeconds": 0.5,
                        "notatedOnset": "0/1",
                        "notatedDuration": "1/4",
                        "voice": 1,
                        "confidence": 0.8,
                        "locked": False,
                    }
                ],
            }
        ],
    )


def test_valid_score_passes():
    validate_score(_valid_score())  # must not raise


def test_missing_pitch_is_rejected():
    score = _valid_score()
    del score["parts"][0]["measures"][0]["events"][0]["pitch"]
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_out_of_range_confidence_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["confidence"] = 1.5
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_schema_v1_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 1
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_part_missing_tempo_bpm_is_rejected():
    score = _valid_score()
    del score["parts"][0]["tempoBpm"]
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_part_missing_confidence_is_rejected():
    score = _valid_score()
    del score["parts"][0]["confidence"]
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_meter_outside_candidate_set_is_rejected():
    score = _valid_score()
    score["parts"][0]["meter"] = "6/8"
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_flat_key_with_music21_notation_is_accepted():
    score = _valid_score()
    score["parts"][0]["key"] = "B- major"
    validate_score(score)  # must not raise — "-" is music21's native flat notation
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_validate.py -v`
Expected: FAIL — old schema still requires only `schemaVersion: 1` and rejects the new part fields as `additionalProperties`

- [ ] **Step 7: Update `_SCORE_SCHEMA`**

```python
# packages/score_schema/src/score_schema/validate.py — replace _SCORE_SCHEMA and add _CONFIDENCE_SCHEMA
_CONFIDENCE_SCHEMA = {
    "type": "object",
    "required": ["tempo", "meter", "key"],
    "properties": {
        "tempo": {"type": "number", "minimum": 0, "maximum": 1},
        "meter": {"type": "number", "minimum": 0, "maximum": 1},
        "key": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "additionalProperties": False,
}

_SCORE_SCHEMA = {
    "type": "object",
    "required": ["schemaVersion", "timeMap", "parts"],
    "properties": {
        "schemaVersion": {"const": 2},
        "timeMap": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["beat", "seconds"],
                "properties": {
                    "beat": {"type": "number"},
                    "seconds": {"type": "number"},
                },
            },
        },
        "parts": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "required": ["instrument", "tempoBpm", "meter", "key", "confidence", "measures"],
                "properties": {
                    "instrument": {"enum": ["guitar", "piano"]},
                    "tempoBpm": {"type": "number", "exclusiveMinimum": 0},
                    "meter": {"enum": ["4/4", "3/4"]},
                    "key": {"type": "string", "pattern": "^[A-G](#|-)? (major|minor)$"},
                    "confidence": _CONFIDENCE_SCHEMA,
                    "measures": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["number", "events"],
                            "properties": {
                                "number": {"type": "integer", "minimum": 1},
                                "events": {"type": "array", "items": _EVENT_SCHEMA},
                            },
                        },
                    },
                },
            },
        },
    },
}
```

`_EVENT_SCHEMA` above this block is unchanged from Phase 1.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests -v`
Expected: PASS (10 tests: 3 from `test_models.py` + 7 from `test_validate.py`)

- [ ] **Step 9: Commit**

```bash
git add packages/score_schema/src/score_schema/models.py packages/score_schema/src/score_schema/validate.py packages/score_schema/tests/test_models.py packages/score_schema/tests/test_validate.py
git commit -m "feat(score-schema): bump canonical score to v2 with tempo/meter/key fields"
```

---

## Task 2: `test_fixtures` — metronome pulse and diatonic melody generators

**Files:**
- Modify: `packages/test_fixtures/src/test_fixtures/generate.py`
- Modify: `packages/test_fixtures/tests/test_generate.py`

**Interfaces:**
- Produces: `write_metronome_pulse_wav(path, bpm=120.0, meter="4/4", duration_s=8.0, sample_rate=22050) -> Path`, `write_diatonic_melody_wav(path, key="C major", duration_s=4.0, sample_rate=22050) -> Path`.
- Consumes: nothing new (same `numpy`/`scipy.io.wavfile` already used by `write_guitar_pluck_wav`).

**Validated design note:** the metronome generator places exactly one click per beat (never a click at a sub-beat subdivision) — this was empirically necessary. An earlier prototype that placed audible clicks at every eighth-note subdivision (to create a "6/8 feel") caused `librosa.beat.beat_track` to lock onto the finest audible pulse rather than a stable tactus, which is exactly what made 6/8 detection unreliable and led to narrowing the meter candidate set to `{4/4, 3/4}` in the first place. One click per beat avoids reintroducing that failure mode.

- [ ] **Step 1: Write the failing test for the metronome pulse generator**

Append to `packages/test_fixtures/tests/test_generate.py`:

```python
def test_write_metronome_pulse_wav_has_correct_duration_and_click_count(tmp_path: Path):
    import numpy as np
    from scipy.io import wavfile

    from test_fixtures.generate import write_metronome_pulse_wav

    out_path = tmp_path / "pulse.wav"
    write_metronome_pulse_wav(out_path, bpm=120.0, meter="4/4", duration_s=8.0, sample_rate=22050)

    sr, data = wavfile.read(str(out_path))
    assert sr == 22050
    # 8s at 120 BPM = 16 beats; each click has a brief attack, so count local peaks
    # above a high threshold as a proxy for "how many clicks landed."
    threshold = 0.5 * np.max(np.abs(data))
    above = np.abs(data) > threshold
    # Count contiguous above-threshold runs (each click's peak sample cluster)
    edges = np.diff(above.astype(int))
    click_count = int(np.sum(edges == 1))
    assert 10 <= click_count <= 20  # ~16 expected, generous tolerance for peak-detection noise


def test_write_metronome_pulse_wav_rejects_unknown_meter(tmp_path: Path):
    import pytest

    from test_fixtures.generate import write_metronome_pulse_wav

    with pytest.raises(KeyError):
        write_metronome_pulse_wav(tmp_path / "bad.wav", bpm=120.0, meter="7/8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package test-fixtures pytest /home/user/AuraAudio/packages/test_fixtures/tests/test_generate.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_metronome_pulse_wav'`

- [ ] **Step 3: Write `write_metronome_pulse_wav`**

Append to `packages/test_fixtures/src/test_fixtures/generate.py`:

```python
_METER_PATTERNS = {
    "4/4": [1.0, 0.4, 0.6, 0.4],
    "3/4": [1.0, 0.4, 0.4],
}


def _click(duration: float, freq: float, amp: float, sample_rate: int) -> np.ndarray:
    t = np.linspace(0, duration, int(sample_rate * duration), endpoint=False)
    envelope = np.exp(-40 * t)
    return amp * envelope * np.sin(2 * np.pi * freq * t)


def write_metronome_pulse_wav(
    path: Path,
    bpm: float = 120.0,
    meter: str = "4/4",
    duration_s: float = 8.0,
    sample_rate: int = 22050,
) -> Path:
    """Synthesize a metronome click track: one click per beat (never per
    subdivision — see the note in the Phase 2 beat/meter/key plan on why
    sub-beat clicks defeat beat-tracking), strong on beat 1, weaker elsewhere,
    at an exact known BPM/meter so tempo and meter detection can be tested
    against ground truth."""
    pattern = _METER_PATTERNS[meter]
    beat_s = 60.0 / bpm
    measure_len = beat_s * len(pattern)
    n_measures = max(int(duration_s / measure_len), 1)
    total_len = n_measures * measure_len + 1.0
    signal = np.zeros(int(total_len * sample_rate))
    for m in range(n_measures):
        for i, amp in enumerate(pattern):
            t0 = m * measure_len + i * beat_s
            c = _click(duration=0.03, freq=1000.0, amp=amp, sample_rate=sample_rate)
            i0 = int(t0 * sample_rate)
            end = min(i0 + len(c), len(signal))
            signal[i0:end] += c[: end - i0]
    signal = (signal / np.max(np.abs(signal)) * 0.9 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package test-fixtures pytest /home/user/AuraAudio/packages/test_fixtures/tests/test_generate.py -v`
Expected: PASS (4 tests — the 2 new ones plus the 2 existing `write_guitar_pluck_wav` tests, unaffected)

- [ ] **Step 5: Write the failing test for the diatonic melody generator**

Append to `packages/test_fixtures/tests/test_generate.py`:

```python
def test_write_diatonic_melody_wav_produces_correct_duration(tmp_path: Path):
    from scipy.io import wavfile

    from test_fixtures.generate import write_diatonic_melody_wav

    out_path = tmp_path / "melody.wav"
    write_diatonic_melody_wav(out_path, key="D major", duration_s=4.0, sample_rate=22050)

    sr, data = wavfile.read(str(out_path))
    assert sr == 22050
    assert abs(len(data) / sr - 4.0) < 0.01


def test_write_diatonic_melody_wav_is_not_silent(tmp_path: Path):
    import numpy as np
    from scipy.io import wavfile

    from test_fixtures.generate import write_diatonic_melody_wav

    out_path = tmp_path / "melody.wav"
    write_diatonic_melody_wav(out_path, key="A minor", duration_s=4.0, sample_rate=22050)
    _, data = wavfile.read(str(out_path))
    assert np.max(np.abs(data)) > 1000
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package test-fixtures pytest /home/user/AuraAudio/packages/test_fixtures/tests/test_generate.py -v`
Expected: FAIL — `ImportError: cannot import name 'write_diatonic_melody_wav'`

- [ ] **Step 7: Write `write_diatonic_melody_wav`**

Append to `packages/test_fixtures/src/test_fixtures/generate.py`:

```python
_NOTE_NAME_TO_SEMITONE = {
    "C": 0, "C#": 1, "D-": 1, "D": 2, "D#": 3, "E-": 3, "E": 4, "F": 5,
    "F#": 6, "G-": 6, "G": 7, "G#": 8, "A-": 8, "A": 9, "A#": 10, "B-": 10, "B": 11,
}
_MAJOR_INTERVALS = [0, 2, 4, 5, 7, 9, 11, 12]
_MINOR_INTERVALS = [0, 2, 3, 5, 7, 8, 10, 12]


def write_diatonic_melody_wav(
    path: Path,
    key: str = "C major",
    duration_s: float = 4.0,
    sample_rate: int = 22050,
) -> Path:
    """Synthesize a short ascending diatonic scale in the given key (music21
    tonic-name convention: flats as '-', e.g. 'B- major'), so key detection
    can be tested against ground truth."""
    tonic_name, mode = key.split(" ")
    tonic_midi = 60 + _NOTE_NAME_TO_SEMITONE[tonic_name]
    intervals = _MAJOR_INTERVALS if mode == "major" else _MINOR_INTERVALS
    pitches = [tonic_midi + iv for iv in intervals]

    note_len = duration_s / len(pitches)
    t_full = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = np.zeros_like(t_full)
    for i, midi in enumerate(pitches):
        freq = 440.0 * (2 ** ((midi - 69) / 12))
        start = i * note_len
        mask = (t_full >= start) & (t_full < start + note_len)
        local_t = t_full[mask] - start
        envelope = np.exp(-3.0 * local_t)
        harmonic = sum(np.sin(2 * np.pi * freq * (h + 1) * local_t) / (h + 1) for h in range(4))
        signal[mask] = envelope * harmonic
    signal = (signal / np.max(np.abs(signal)) * 0.8 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package test-fixtures pytest /home/user/AuraAudio/packages/test_fixtures/tests -v`
Expected: PASS (6 tests total)

- [ ] **Step 9: Commit**

```bash
git add packages/test_fixtures/src/test_fixtures/generate.py packages/test_fixtures/tests/test_generate.py
git commit -m "feat(test-fixtures): add metronome pulse and diatonic melody generators"
```

---

## Task 3: worker `structure` stage — tempo, meter, and key detection

**Files:**
- Create: `workers/transcription/src/aura_worker/stages/structure.py`
- Modify: `workers/transcription/src/aura_worker/stage_runner.py`
- Create: `workers/transcription/tests/test_structure.py`

**Interfaces:**
- Consumes: `StageContext`/`find_cached_artifact`/`save_artifact` (Task 10 of the Phase 1 plan), `NoteEvent`/`JobErrorCode` (`score_schema.models`), `write_metronome_pulse_wav`/`write_diatonic_melody_wav` (Task 2), `inference.run` (Phase 1, reused in the key-detection test to get real transcribed notes rather than hand-built ones).
- Produces: `StructureResult(tempo_bpm: float, meter: str, key: str, tempo_confidence: float, meter_confidence: float, key_confidence: float)` dataclass; `stages.structure.run(ctx: StageContext, normalized_path: Path, notes: list[NoteEvent]) -> StructureResult`. **Assumes `notes` is non-empty** — this is safe because `inference.run` (which always runs first in the pipeline) already raises `JobFailure(NO_MUSIC_DETECTED)` on zero notes, so `structure.run` never has to handle that case itself. Raises `JobFailure(JobErrorCode.MODEL_FAILED, ...)` when `beat_track` finds fewer than 2 beats.

- [ ] **Step 1: Write the failing test for tempo detection**

```python
# workers/transcription/tests/test_structure.py
from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import structure
from score_schema.models import NoteEvent
from test_fixtures.generate import write_diatonic_melody_wav, write_metronome_pulse_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


# structure.run assumes non-empty notes (guaranteed in the real pipeline by
# inference.run's NO_MUSIC_DETECTED check) — tests that don't care about key
# detection still pass a minimal single-note list rather than [], since an
# empty stream would make music21's key analysis raise.
_PLACEHOLDER_NOTES = [NoteEvent(pitch=60, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]


def test_structure_detects_tempo_within_tolerance(db_session, sample_job, workdir):
    wav_path = workdir / "pulse.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="4/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    # Empirically validated tolerance: beat_track showed a consistent ~3 BPM
    # low bias against a 120 BPM synthetic click fixture during spec prototyping.
    assert abs(result.tempo_bpm - 120.0) <= 5.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aura_worker.stages.structure'`

- [ ] **Step 3: Write the initial implementation (tempo detection only)**

```python
# workers/transcription/src/aura_worker/stages/structure.py
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

    result = StructureResult(
        tempo_bpm=tempo_bpm, meter="4/4", key="C major",
        tempo_confidence=tempo_confidence, meter_confidence=0.0, key_confidence=0.0,
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: PASS (1 test)

- [ ] **Step 5: Write the failing tests for meter detection**

Append to `workers/transcription/tests/test_structure.py`:

```python
def test_structure_detects_four_four_meter(db_session, sample_job, workdir):
    wav_path = workdir / "pulse44.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="4/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    assert result.meter == "4/4"


def test_structure_detects_three_four_meter(db_session, sample_job, workdir):
    wav_path = workdir / "pulse34.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="3/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    assert result.meter == "3/4"
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: FAIL — both new tests fail because `run()` always returns the hardcoded `meter="4/4"`, so the 3/4 case fails (the 4/4 case passes by coincidence)

- [ ] **Step 7: Implement the validated accent-periodicity meter scorer**

```python
# workers/transcription/src/aura_worker/stages/structure.py — add these functions above run()
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
```

Then replace the hardcoded meter line inside `run()`:

```python
    tempo_confidence = _tempo_confidence(beat_times)
    meter, meter_confidence = _detect_meter(y, sr, beat_times)

    result = StructureResult(
        tempo_bpm=tempo_bpm, meter=meter, key="C major",
        tempo_confidence=tempo_confidence, meter_confidence=meter_confidence, key_confidence=0.0,
    )
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: PASS (3 tests)

- [ ] **Step 9: Write the failing test for key detection**

Append to `workers/transcription/tests/test_structure.py`:

```python
def test_structure_detects_known_key_from_real_transcription(db_session, sample_job, workdir):
    from aura_worker.stages import inference

    wav_path = workdir / "melody.wav"
    write_diatonic_melody_wav(wav_path, key="C major", duration_s=4.0, sample_rate=22050)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    notes = inference.run(ctx, normalized_path=wav_path)
    result = structure.run(ctx, normalized_path=wav_path, notes=notes)

    assert result.key == "C major"
```

- [ ] **Step 10: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: FAIL — `result.key` is still the hardcoded `"C major"` placeholder, so this test happens to pass by coincidence too; **do not treat that as green** — confirm by temporarily changing the fixture's `key=` argument to `"D major"` and re-running to see it still (wrongly) report `"C major"`, then revert before Step 11. This is a case where the test can't distinguish "hardcoded" from "correct" without a second data point — Step 11 removes the hardcoding, and Step 12 covers the risk properly by not relying on this test alone.

- [ ] **Step 11: Implement key detection**

```python
# workers/transcription/src/aura_worker/stages/structure.py — add above run()
def _detect_key(notes: list[NoteEvent]) -> tuple[str, float]:
    from music21 import note as m21_note
    from music21 import stream

    s = stream.Stream()
    for n in notes:
        s.append(m21_note.Note(n.pitch))
    analyzed = s.analyze("krumhansl")  # NOT "key" — verified during implementation that
    # the bare 'key' method name does not actually use Krumhansl-Schmuckler weighting and
    # misclassifies clean major scales as their relative minor; request the algorithm by name.
    key_str = f"{analyzed.tonic.name} {analyzed.mode}"
    confidence = float(np.clip(analyzed.correlationCoefficient, 0.0, 1.0))
    return key_str, confidence
```

Then replace the hardcoded key line inside `run()`:

```python
    tempo_confidence = _tempo_confidence(beat_times)
    meter, meter_confidence = _detect_meter(y, sr, beat_times)
    key, key_confidence = _detect_key(notes)

    result = StructureResult(
        tempo_bpm=tempo_bpm, meter=meter, key=key,
        tempo_confidence=tempo_confidence, meter_confidence=meter_confidence, key_confidence=key_confidence,
    )
```

- [ ] **Step 12: Add a second key-detection test with a different key, to actually prove the hardcoding is gone**

Append to `workers/transcription/tests/test_structure.py`:

```python
def test_structure_detects_a_different_known_key(db_session, sample_job, workdir):
    from aura_worker.stages import inference

    wav_path = workdir / "melody_d.wav"
    write_diatonic_melody_wav(wav_path, key="D major", duration_s=4.0, sample_rate=22050)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    notes = inference.run(ctx, normalized_path=wav_path)
    result = structure.run(ctx, normalized_path=wav_path, notes=notes)

    assert result.key == "D major"
```

- [ ] **Step 13: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: PASS (5 tests). If `test_structure_detects_a_different_known_key` fails because `basic-pitch` mistranscribed a pitch in the synthetic melody (unlikely given Task 13 of the Phase 1 plan already validated `basic-pitch` against similarly-synthesized fixtures, but possible), debug by printing the `notes` list's pitches before asserting — `music21`'s key analysis is only as good as the input pitch classes.

- [ ] **Step 14: Write the failing test for the no-beats-found error path**

Append to `workers/transcription/tests/test_structure.py`:

```python
def test_structure_raises_model_failed_on_silence(db_session, sample_job, workdir):
    import numpy as np
    from scipy.io import wavfile

    silence = np.zeros(22050 * 4, dtype=np.int16)
    wav_path = workdir / "silence.wav"
    wavfile.write(str(wav_path), 22050, silence)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    try:
        structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)
        assert False, "expected JobFailure"
    except JobFailure as exc:
        assert exc.code.value == "MODEL_FAILED"
```

- [ ] **Step 15: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: PASS (6 tests) — this should already pass given Step 3's `len(beat_times) < 2` guard; if it fails, `librosa.beat.beat_track` may be returning a small number of spurious beats on pure silence rather than zero — inspect `beat_times` directly to confirm before changing the threshold.

- [ ] **Step 16: Write the failing test for cache/resume behavior**

Append to `workers/transcription/tests/test_structure.py`:

```python
def test_structure_second_call_resumes_without_recompute(db_session, sample_job, workdir, monkeypatch):
    wav_path = workdir / "pulse_resume.wav"
    write_metronome_pulse_wav(wav_path, bpm=120.0, meter="4/4", duration_s=8.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    first = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    import librosa

    def fail_if_called(*args, **kwargs):
        raise AssertionError("librosa.load should not be re-invoked on a cached structure stage")

    monkeypatch.setattr(librosa, "load", fail_if_called)

    second = structure.run(ctx, normalized_path=wav_path, notes=_PLACEHOLDER_NOTES)

    assert second == first
```

- [ ] **Step 17: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_structure.py -v`
Expected: PASS (7 tests)

- [ ] **Step 18: Wire `structure` into `STAGE_PROGRESS`**

```python
# workers/transcription/src/aura_worker/stage_runner.py
STAGE_PROGRESS = {
    "probe": 10,
    "normalize": 25,
    "inference": 55,
    "structure": 65,
    "quantize": 75,
    "export": 100,
}
```

- [ ] **Step 19: Run the full worker test suite to confirm nothing else regressed**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests -v`
Expected: PASS — all Phase 1 worker tests plus the 7 new `test_structure.py` tests. (`test_quantize.py` and `test_export.py` still reference the Phase 1 signatures at this point in the plan and are updated in Tasks 4 and 5 — if they fail here, that is expected and resolved there, not a regression to chase now.)

- [ ] **Step 20: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/structure.py workers/transcription/src/aura_worker/stage_runner.py workers/transcription/tests/test_structure.py
git commit -m "feat(worker): add structure stage for tempo/meter/key detection"
```

---

## Task 4: `quantize` stage — consume `StructureResult`

**Files:**
- Modify: `workers/transcription/src/aura_worker/stages/quantize.py`
- Modify: `workers/transcription/tests/test_quantize.py`

**Interfaces:**
- Consumes: `StructureResult` (Task 3), `build_score`/`validate_score` v2 signature (Task 1).
- Produces: `stages.quantize.run(ctx, notes, structure) -> dict` — signature gains a required `structure: StructureResult` parameter. `STAGE_VERSION` bumps `1` → `2` (output shape changed, so a Phase-1-cached artifact must not be reused).

- [ ] **Step 1: Write the failing test for the new signature and v2 output**

Replace `workers/transcription/tests/test_quantize.py` in full:

```python
from score_schema.models import NoteEvent
from score_schema.validate import validate_score

from aura_worker.stage_runner import StageContext
from aura_worker.stages import quantize
from aura_worker.stages.structure import StructureResult


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def _structure_120_four_four() -> StructureResult:
    return StructureResult(
        tempo_bpm=120.0, meter="4/4", key="C major",
        tempo_confidence=0.9, meter_confidence=0.8, key_confidence=0.7,
    )


def test_quantize_snaps_notes_to_sixteenth_grid_and_produces_valid_v2_score(db_session, sample_job, workdir):
    notes = [
        NoteEvent(pitch=64, onset_s=0.02, offset_s=0.48, velocity=90, confidence=0.9),
        NoteEvent(pitch=67, onset_s=0.53, offset_s=0.97, velocity=85, confidence=0.85),
    ]

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, _structure_120_four_four())

    validate_score(score)  # must not raise
    part = score["parts"][0]
    assert part["tempoBpm"] == 120.0
    assert part["meter"] == "4/4"
    assert part["key"] == "C major"
    assert part["confidence"] == {"tempo": 0.9, "meter": 0.8, "key": 0.7}
    events = part["measures"][0]["events"]
    assert events[0]["pitch"] == 64
    assert events[0]["notatedOnset"] == "0/1"
    assert events[0]["notatedDuration"] == "1/4"

    from aura_api.models import ScoreRevision
    revision = db_session.query(ScoreRevision).filter_by(project_id=sample_job.project_id).one()
    assert revision.revision == 0
    assert revision.score_json["schemaVersion"] == 2


def test_quantize_places_far_notes_in_later_measures(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=60, onset_s=9.0, offset_s=9.4, velocity=80, confidence=0.7)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, _structure_120_four_four())

    # 9.0s at 120 BPM (0.5s/beat) = beat 18 = measure 5 (4 beats/measure, 1-indexed)
    measure_numbers = [m["number"] for m in score["parts"][0]["measures"]]
    assert 5 in measure_numbers


def test_quantize_respects_three_four_measure_length(db_session, sample_job, workdir):
    structure = StructureResult(
        tempo_bpm=120.0, meter="3/4", key="C major",
        tempo_confidence=0.9, meter_confidence=0.8, key_confidence=0.7,
    )
    notes = [NoteEvent(pitch=60, onset_s=3.2, offset_s=3.5, velocity=80, confidence=0.7)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes, structure)

    # seconds_per_beat = 0.5; onset_beats = 3.2/0.5 = 6.4, snapped to nearest
    # 1/4-beat = 6.5; measure_number = int(6.5 // 3) + 1 = 3 (3 beats/measure).
    measure_numbers = [m["number"] for m in score["parts"][0]["measures"]]
    assert measure_numbers == [3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_quantize.py -v`
Expected: FAIL — `quantize.run() missing 1 required positional argument: 'structure'`

- [ ] **Step 3: Rewrite `quantize.py`**

```python
# workers/transcription/src/aura_worker/stages/quantize.py — full replacement
from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from aura_worker.stages.structure import StructureResult
from score_schema.models import NoteEvent, build_score
from score_schema.validate import validate_score

STAGE_VERSION = 2
GRID_BEATS = Fraction(1, 4)  # snap to 16th notes (1/4 of a beat, since a beat = quarter note)
BEATS_PER_MEASURE = {"4/4": 4, "3/4": 3}


def _seconds_to_beats(seconds: float, seconds_per_beat: float) -> Fraction:
    raw_beats = Fraction(seconds / seconds_per_beat).limit_denominator(64)
    return round(raw_beats / GRID_BEATS) * GRID_BEATS


def _beats_to_notated_fraction(beats: Fraction) -> str:
    """Notated duration/onset as a fraction of a whole note (4 beats)."""
    whole_note_fraction = beats / 4
    return f"{whole_note_fraction.numerator}/{whole_note_fraction.denominator}"


def run(ctx: StageContext, notes: list[NoteEvent], structure: StructureResult) -> dict:
    cached = find_cached_artifact(ctx, "quantize", STAGE_VERSION)
    if cached is not None:
        return json.loads(ctx.storage.get_bytes(cached.object_key))

    seconds_per_beat = 60.0 / structure.tempo_bpm
    beats_per_measure = BEATS_PER_MEASURE[structure.meter]

    measures: dict[int, list[dict]] = {}

    for i, note in enumerate(notes):
        onset_beats = _seconds_to_beats(note.onset_s, seconds_per_beat)
        offset_beats = _seconds_to_beats(note.offset_s, seconds_per_beat)
        duration_beats = max(offset_beats - onset_beats, GRID_BEATS)

        measure_number = int(onset_beats // beats_per_measure) + 1
        onset_within_measure = onset_beats - (measure_number - 1) * beats_per_measure

        event = {
            "id": f"note_{i:02d}",
            "pitch": note.pitch,
            "onsetSeconds": note.onset_s,
            "offsetSeconds": note.offset_s,
            "notatedOnset": _beats_to_notated_fraction(onset_within_measure),
            "notatedDuration": _beats_to_notated_fraction(duration_beats),
            "voice": 1,
            "confidence": note.confidence,
            "locked": False,
        }
        measures.setdefault(measure_number, []).append(event)

    measure_list = [
        {"number": number, "events": events}
        for number, events in sorted(measures.items())
    ]
    time_map = [
        {"beat": 0, "seconds": 0.0},
        {"beat": 1, "seconds": seconds_per_beat},
    ]

    score = build_score(
        instrument=ctx.job.project.instrument,
        tempo_bpm=structure.tempo_bpm,
        meter=structure.meter,
        key=structure.key,
        confidence={
            "tempo": structure.tempo_confidence,
            "meter": structure.meter_confidence,
            "key": structure.key_confidence,
        },
        time_map=time_map,
        measures=measure_list,
    )
    validate_score(score)

    object_key = f"jobs/{ctx.job.id}/stage/score.json"
    payload = json.dumps(score).encode()
    ctx.storage.put_bytes(object_key, payload)

    from aura_api.models import ScoreRevision

    revision = ScoreRevision(
        project_id=ctx.job.project_id, parent_id=None, revision=0,
        score_json=score, created_by="system",
    )
    ctx.session.add(revision)
    save_artifact(
        ctx, "quantize", STAGE_VERSION, object_key=object_key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={"measure_count": len(measure_list)},
    )

    return score
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_quantize.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/quantize.py workers/transcription/tests/test_quantize.py
git commit -m "feat(worker): quantize stage consumes detected tempo/meter instead of hardcoded constants"
```

---

## Task 5: `musicxml/export.py` — real time signature, tempo, key, and enharmonic spelling

**Files:**
- Modify: `packages/musicxml/src/musicxml/export.py`
- Modify: `packages/musicxml/tests/test_export.py`
- Modify: `packages/musicxml/tests/test_validate.py`

**Interfaces:**
- Consumes: v2 score dict (Task 1) with `tempoBpm`/`meter`/`key` on each part.
- Produces: `score_json_to_musicxml(score, out_path) -> Path` — same signature as Phase 1, but now reads real values instead of hardcoding `TimeSignature("4/4")`/`MetronomeMark(120)`, and spells each note using the detected key instead of a fixed default.

**Validated design notes** (confirmed directly against `music21` 10.5.0's actual XML output before writing this task, not assumed):
- `TimeSignature` and `Key` inserted at the `Part` level correctly propagate into each measure's `<attributes>` in the exported MusicXML.
- `MetronomeMark` inserted at the `Part` level is **silently dropped** — it must be inserted into the first `Measure` instead, where it correctly produces both `<metronome><per-minute>N</per-minute></metronome>` and `<sound tempo="N" />`.
- `Key.pitches` returns the 8 correctly-spelled scale-degree pitches for a given `Key` (including the octave repeat of the tonic at index 7); `Key.sharps` is negative for flat keys. Both are used for enharmonic spelling below.

- [ ] **Step 1: Write the failing test for the updated score fixture and new assertions**

Replace `packages/musicxml/tests/test_export.py` in full:

```python
from pathlib import Path

from score_schema.models import build_score

from musicxml.export import score_json_to_musicxml


def _sample_score(meter: str = "4/4", key: str = "C major", tempo_bpm: float = 120.0):
    return build_score(
        instrument="guitar",
        tempo_bpm=tempo_bpm,
        meter=meter,
        key=key,
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": 0.5}],
        measures=[
            {
                "number": 1,
                "events": [
                    {
                        "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.9, "locked": False,
                    },
                    {
                        "id": "note_01", "pitch": 67, "onsetSeconds": 0.5, "offsetSeconds": 1.0,
                        "notatedOnset": "1/4", "notatedDuration": "1/4", "voice": 1,
                        "confidence": 0.85, "locked": False,
                    },
                ],
            }
        ],
    )


def test_score_json_to_musicxml_writes_a_file(tmp_path: Path):
    out_path = tmp_path / "out.musicxml"
    result_path = score_json_to_musicxml(_sample_score(), out_path)

    assert result_path == out_path
    assert out_path.exists()
    content = out_path.read_text()
    assert "<score-partwise" in content
    assert content.count("<note>") == 2


def test_score_json_to_musicxml_uses_detected_time_signature(tmp_path: Path):
    out_path = tmp_path / "out34.musicxml"
    score_json_to_musicxml(_sample_score(meter="3/4"), out_path)
    content = out_path.read_text()
    assert "<beats>3</beats>" in content
    assert "<beat-type>4</beat-type>" in content


def test_score_json_to_musicxml_uses_detected_tempo(tmp_path: Path):
    out_path = tmp_path / "out_tempo.musicxml"
    score_json_to_musicxml(_sample_score(tempo_bpm=90.0), out_path)
    content = out_path.read_text()
    assert "<per-minute>90</per-minute>" in content
    assert 'sound tempo="90"' in content


def test_score_json_to_musicxml_uses_detected_key(tmp_path: Path):
    out_path = tmp_path / "out_key.musicxml"
    score_json_to_musicxml(_sample_score(key="D major"), out_path)
    content = out_path.read_text()
    assert "<fifths>2</fifths>" in content
    assert "<mode>major</mode>" in content


def test_score_json_to_musicxml_spells_pitch_using_key_context(tmp_path: Path):
    # Pitch 66 (F#/Gb) is diatonic in D major as F#, but NOT diatonic in F
    # major (which uses flats) — so the same MIDI pitch should spell
    # differently depending on the score's detected key.
    score_sharp_key = _sample_score(key="D major")
    score_sharp_key["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 66, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path = tmp_path / "sharp.musicxml"
    score_json_to_musicxml(score_sharp_key, out_path)
    assert "<step>F</step>" in out_path.read_text()

    score_flat_key = _sample_score(key="F major")
    score_flat_key["parts"][0]["measures"][0]["events"] = [{
        "id": "note_00", "pitch": 66, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False,
    }]
    out_path2 = tmp_path / "flat.musicxml"
    score_json_to_musicxml(score_flat_key, out_path2)
    assert "<step>G</step>" in out_path2.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests/test_export.py -v`
Expected: FAIL — `build_score() missing required keyword-only arguments` on `_sample_score`, and once that's visibly the blocker, the file still won't run until Step 3 lands

- [ ] **Step 3: Rewrite `export.py`**

```python
# packages/musicxml/src/musicxml/export.py — full replacement
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from music21 import duration, instrument, key as m21_key, meter as m21_meter, note, pitch as m21_pitch, stream, tempo


def _notated_fraction_to_quarter_length(value: str) -> float:
    """A notated value like '1/4' means one quarter of a whole note (a
    quarter note = 1.0 quarterLength in music21), so quarterLength = 4 * fraction."""
    return float(Fraction(value) * 4)


def _spell_pitch(midi_number: int, key_obj: m21_key.Key) -> m21_pitch.Pitch:
    """Spell a MIDI pitch using the detected key's diatonic collection where
    possible, falling back to the key's sharp/flat preference for chromatic
    (non-diatonic) tones."""
    octave = midi_number // 12 - 1
    pc = midi_number % 12
    diatonic_by_pc = {p.pitchClass: p.name for p in key_obj.pitches[:7]}
    if pc in diatonic_by_pc:
        return m21_pitch.Pitch(f"{diatonic_by_pc[pc]}{octave}")

    default = m21_pitch.Pitch(ps=midi_number)  # sharp-preferred by default
    if key_obj.sharps < 0 and default.accidental is not None and default.accidental.name == "sharp":
        return default.getEnharmonic()
    return default


def score_json_to_musicxml(score: dict, out_path: Path) -> Path:
    part_data = score["parts"][0]
    tonic_name, mode = part_data["key"].split(" ")
    key_obj = m21_key.Key(tonic_name, mode)

    m21_part = stream.Part()
    m21_part.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    m21_part.insert(0, key_obj)
    m21_part.insert(0, instrument.Guitar() if part_data["instrument"] == "guitar" else instrument.Piano())

    is_first_measure = True
    for measure_data in part_data["measures"]:
        m21_measure = stream.Measure(number=measure_data["number"])
        if is_first_measure:
            # MetronomeMark must live in the Measure, not the Part — see the
            # "Validated design notes" in the implementation plan: inserting
            # it at the Part level silently drops it from the exported XML.
            m21_measure.insert(0, tempo.MetronomeMark(number=part_data["tempoBpm"]))
            is_first_measure = False
        for event in measure_data["events"]:
            n = note.Note(_spell_pitch(event["pitch"], key_obj))
            n.duration = duration.Duration(_notated_fraction_to_quarter_length(event["notatedDuration"]))
            m21_measure.append(n)
        m21_part.append(m21_measure)

    m21_score = stream.Score()
    m21_score.insert(0, m21_part)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m21_score.write("musicxml", fp=str(out_path))
    return out_path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests/test_export.py -v`
Expected: PASS (5 tests)

- [ ] **Step 5: Update `test_validate.py`'s shared fixture usage**

`packages/musicxml/tests/test_validate.py` imports `_sample_score` from `test_export.py` via `from .test_export import _sample_score` — since Step 3/4 already updated `_sample_score` in place (same function name, now with required `meter`/`key`/`tempo_bpm` defaults), no changes are needed to `test_validate.py`'s imports or test bodies. Confirm this by running it directly.

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests/test_validate.py -v`
Expected: PASS (4 tests) — if the barline-crossing-tie test (`test_reopen_and_check_counts_a_barline_crossing_note_once`, added in the Phase 1 plan's Task 18 bugfix) fails, check whether it constructs a raw score dict rather than calling `_sample_score()`/`build_score()` directly — the Phase 1 version of that test builds its `score` dict inline via `build_score(...)`, which now requires the v2 keyword arguments; add `tempo_bpm=120.0, meter="4/4", key="C major", confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7}` to that call if so.

- [ ] **Step 6: Run the full musicxml suite**

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests -v`
Expected: PASS (9 tests: 5 from `test_export.py` + 4 from `test_validate.py`)

- [ ] **Step 7: Commit**

```bash
git add packages/musicxml/src/musicxml/export.py packages/musicxml/tests/test_export.py packages/musicxml/tests/test_validate.py
git commit -m "feat(musicxml): render detected time signature, tempo, and key; key-aware enharmonic spelling"
```

---

## Task 6: export stage MIDI tempo + runner wiring

**Files:**
- Modify: `workers/transcription/src/aura_worker/stages/export.py`
- Modify: `workers/transcription/tests/test_export.py`
- Modify: `workers/transcription/src/aura_worker/runner.py`

**Interfaces:**
- Consumes: `structure.run` (Task 3), v2 score dict's `tempoBpm` field (Task 1/4).
- Produces: `stages.export.run(ctx, notes, score) -> dict` — **signature unchanged** from Phase 1; internally now reads `score["parts"][0]["tempoBpm"]` instead of hardcoding `120` for the MIDI tempo track. `runner.run_transcription_job` gains a `structure.run` call between `inference` and `quantize`.

- [ ] **Step 1: Write the failing test for MIDI tempo reflecting the score**

Replace the test body in `workers/transcription/tests/test_export.py`'s existing test with an added assertion, and add a new test — replace the file in full:

```python
import mido

from score_schema.models import NoteEvent, build_score

from aura_worker.stage_runner import StageContext
from aura_worker.stages import export as export_stage


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def _sample_score(tempo_bpm: float = 120.0):
    return build_score(
        instrument="guitar",
        tempo_bpm=tempo_bpm,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [{
                "id": "note_00", "pitch": 64, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                "confidence": 0.9, "locked": False,
            }],
        }],
    )


def test_export_stage_writes_midi_and_musicxml_and_creates_export_rows(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=64, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]
    score = _sample_score()

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = export_stage.run(ctx, notes=notes, score=score)

    assert result["midi_key"].endswith(".mid")
    assert result["musicxml_key"].endswith(".musicxml")
    assert any(k == result["midi_key"] for k in storage.objects)
    assert any(k == result["musicxml_key"] for k in storage.objects)

    from aura_api.models import Export, TranscriptionJob

    exports = db_session.query(Export).filter_by(job_id=sample_job.id).all()
    formats = {e.format for e in exports}
    assert formats == {"midi", "musicxml"}
    assert all(e.status == "succeeded" for e in exports)

    refreshed_job = db_session.get(TranscriptionJob, sample_job.id)
    assert refreshed_job.status == "succeeded"


def test_export_stage_midi_uses_detected_tempo(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=64, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]
    score = _sample_score(tempo_bpm=90.0)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = export_stage.run(ctx, notes=notes, score=score)

    midi_bytes = storage.objects[result["midi_key"]]
    (workdir / "check.mid").write_bytes(midi_bytes)
    mid = mido.MidiFile(str(workdir / "check.mid"))
    tempo_messages = [msg for track in mid.tracks for msg in track if msg.type == "set_tempo"]
    assert len(tempo_messages) == 1
    assert mido.tempo2bpm(tempo_messages[0].tempo) == 90.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_export.py -v`
Expected: FAIL on `test_export_stage_midi_uses_detected_tempo` — `mido.tempo2bpm(...)` returns `120.0`, not `90.0`, since `_write_midi` still hardcodes `mido.bpm2tempo(120)`

- [ ] **Step 3: Update `export.py` to read tempo from the score**

```python
# workers/transcription/src/aura_worker/stages/export.py — modify _write_midi and run
def _write_midi(notes: list[NoteEvent], out_path, tempo_bpm: float) -> None:
    import mido

    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    ticks_per_beat = mid.ticks_per_beat  # default 480
    tempo_us = mido.bpm2tempo(tempo_bpm)
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))

    events = []
    for note_event in notes:
        events.append((note_event.onset_s, "on", note_event))
        events.append((note_event.offset_s, "off", note_event))
    events.sort(key=lambda e: (e[0], e[1] == "on"))

    seconds_per_tick = (tempo_us / 1_000_000) / ticks_per_beat
    last_tick = 0
    for seconds, kind, note_event in events:
        tick = int(seconds / seconds_per_tick)
        delta = max(tick - last_tick, 0)
        last_tick = tick
        msg_type = "note_on" if kind == "on" else "note_off"
        velocity = note_event.velocity if kind == "on" else 0
        track.append(mido.Message(msg_type, note=note_event.pitch, velocity=velocity, time=delta))

    mid.save(str(out_path))
```

And in `run()`, change the `_write_midi` call site:

```python
    tempo_bpm = score["parts"][0]["tempoBpm"]
    midi_path = ctx.workdir / "output.mid"
    musicxml_path = ctx.workdir / "output.musicxml"

    _write_midi(notes, midi_path, tempo_bpm)
    score_json_to_musicxml(score, musicxml_path)
```

(replacing the existing `_write_midi(notes, midi_path)` two-argument call — everything else in `run()` is unchanged.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_export.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Wire `structure.run` into the pipeline**

```python
# workers/transcription/src/aura_worker/runner.py — modify the imports and the pipeline body
from aura_worker.stages import inference, normalize, probe, quantize, structure
```

(replacing the existing `from aura_worker.stages import inference, normalize, probe, quantize` line — adding `structure`.)

```python
            probe.run(ctx)
            # probe.run downloads the source to ctx.workdir/"source"/"input" (a fixed
            # convention, not a return value) so normalize.run can find it on resume.
            normalized_path = normalize.run(ctx, source_path=ctx.workdir / "source" / "input")
            notes = inference.run(ctx, normalized_path=normalized_path)
            structure_result = structure.run(ctx, normalized_path=normalized_path, notes=notes)
            score = quantize.run(ctx, notes, structure_result)
            export_stage.run(ctx, notes=notes, score=score)
```

(replacing the existing five-line pipeline body — inserting the `structure_result` line and passing it to `quantize.run`.)

- [ ] **Step 6: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/export.py workers/transcription/tests/test_export.py workers/transcription/src/aura_worker/runner.py
git commit -m "feat(worker): use detected tempo for MIDI export; wire structure stage into the pipeline"
```

---

## Task 7: full workspace verification

**Files:**
- None created or modified — this task is verification only, matching Task 18's role in the Phase 1 plan.

**Interfaces:**
- Exercises every interface touched by Tasks 1-6 together, end-to-end, through the existing `apps/api/tests/test_e2e_pipeline.py` (unchanged — it calls `run_transcription_job`, which now runs the `structure` stage internally; the test's own assertions don't need to know about tempo/meter/key to keep passing).

- [ ] **Step 1: Confirm local infra is up**

Run: `redis-cli ping` (expect `PONG`), `pg_isready` (expect `accepting connections`), and confirm the object storage endpoint used by `S3_ENDPOINT_URL` responds to a basic request. If any are down, bring them back up the same way they were started for the Phase 1 plan's execution (native `postgres`/`redis-server`, or `docker compose -f infra/docker-compose.yml up -d` where Docker Hub pulls are reachable).

- [ ] **Step 2: Run the full workspace test suite**

Run: `make test`
Expected: every package's suite passes, including the untouched `apps/api/tests/test_e2e_pipeline.py` — its `write_guitar_pluck_wav` fixture (4 notes, no strong periodic pulse) produces only 2-3 `beat_track` beats when run through the new `structure` stage; this was verified during planning to complete without raising (the `< 2 beats` guard is not triggered) and to still drive the job to `status: succeeded`, even though the resulting meter/key guess on such sparse input is low-confidence and not asserted by that test.

- [ ] **Step 3: Spot-check one full run's MusicXML output**

Run (adjust paths/venv activation to match however the Phase 1 plan's execution set up the local environment):

```bash
uv run --package test-fixtures python -c "
from pathlib import Path
from test_fixtures.generate import write_metronome_pulse_wav
write_metronome_pulse_wav(Path('/tmp/rhythm_check.wav'), bpm=100.0, meter='3/4', duration_s=8.0)
"
```

Then run this fixture through the pipeline exactly as the Phase 1 plan's Task 19 manual smoke test did (`POST /v1/uploads` → upload bytes → `POST /v1/projects` → `POST /v1/projects/{id}/transcriptions` → poll `GET /v1/jobs/{id}` until `succeeded` → `GET /v1/exports/{id}` for the `musicxml` format → download and open the file). Confirm it contains `<beats>3</beats>` and a `<per-minute>` value near 100 — this is the manual equivalent of Task 18's automated e2e test, run once against a rhythmically well-defined fixture (unlike the automated test's arrhythmic Phase 1 fixture) as a final sanity check that detection actually works end-to-end, not just in isolated stage tests.

- [ ] **Step 4: Update the Phase 1 plan's task list status (optional bookkeeping)**

No file changes required — this step is a reminder, not an action: if task tracking (e.g. a `TaskList`) was used for the Phase 1 plan's execution, mark this sub-project's tasks complete there too, matching the pattern established during that execution.

## Definition of Done

Matches the spec's Definition of Done exactly:

- [ ] A developer can point the pipeline at a fixture with a known BPM/meter/key and get back detected values within stated tolerances (±5 BPM tempo, exact meter match for `{4/4, 3/4}`, exact key match), with confidence scores stored on the canonical score.
- [ ] `quantize.py` and `musicxml/export.py` contain no hardcoded tempo/meter/key constant (verified: `grep -n "= 120\|TimeSignature(\"4/4\")\|MetronomeMark(number=120)"` across both files returns nothing left over from Phase 1).
- [ ] Full workspace test suite passes (Task 7).
