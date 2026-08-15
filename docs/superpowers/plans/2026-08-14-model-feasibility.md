# AuraAudio Model Feasibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible CPU-only benchmark that compares two solo-piano transcription engines on 40 rights-cleared clips and produces a go, narrow, or stop decision for AuraAudio desktop MVP.

**Architecture:** A Python 3.11 benchmark package defines engine-neutral note events, prepares aligned audio/MIDI fixtures, runs each engine in an isolated subprocess, measures quality/runtime/memory, and generates Markdown plus machine-readable reports. Spotify Basic Pitch 0.4.0 using its bundled ONNX model is the lightweight baseline; Piano Transcription Inference 0.0.6 using the high-resolution ByteDance checkpoint is the piano-specific comparator.

**Tech Stack:** Python 3.11, uv, pytest, Hypothesis, Pydantic 2, Typer, NumPy, pretty_midi, mir_eval, psutil, FFmpeg/ffprobe, Basic Pitch 0.4.0, ONNX Runtime CPU, Piano Transcription Inference 0.0.6, PyTorch CPU.

## Global Constraints

- Run entirely locally; normal benchmark execution must make no network calls.
- Target Windows 10/11 and macOS on CPU-only hardware with 16 GB RAM.
- Maximum clip duration is 300 seconds.
- Total application/worker peak resident memory must not exceed 12 GB.
- Use exactly 40 rights-cleared solo-piano clips: 10 clean, 10 noisy, 10 room-recorded, and 10 consumer-device recordings.
- Do not commit audio, MIDI references, model weights, generated predictions, or benchmark result data containing private paths.
- Store SHA-256 for every input, reference, model, configuration, and output artifact.
- Keep performed time in seconds; do not quantize notes during Phase 0.
- Evaluate note-onset F1, note-onset-plus-offset F1, frame F1, completion rate, real-time factor, peak resident memory, package size, and human correction effort.
- A candidate passes only when completion rate is at least 95%, peak memory is at most 12 GB, p95 real-time factor is at most 3.0, clean-cohort median onset F1 is at least 0.80, degraded-cohort median onset F1 is at least 0.65, clean-cohort median onset-plus-offset F1 is at least 0.60, and median correction time on the review subset is at most twice clip duration.
- Prefer the passing engine with highest overall median onset F1; break ties within 0.01 F1 by lower p95 real-time factor, then smaller installed size.
- Basic Pitch is Apache-2.0 and supports polyphonic single-instrument transcription. Its current package includes ONNX serialization. Piano Transcription Inference exposes CPU execution but its upstream package is old and must be treated as an adapter risk.
- MAESTRO data may be used only when its CC BY-NC-SA 4.0 terms fit the project. Private/user-owned recordings require written rights evidence in the local manifest.

## Plan boundary

This plan implements only **Sub-project 1: Model feasibility** from the approved design. It does not build PySide6 UI, project storage, notation, editing, exports, or installers. Those receive separate plans after this gate passes.

## File map

```text
.gitignore                                      # Excludes private media, weights, runs, and reports with local paths
pyproject.toml                                  # Python package, dependency groups, test/lint configuration
uv.lock                                         # Exact resolved dependencies
README.md                                       # Developer setup and benchmark commands
apps/desktop/auraaudio/__init__.py              # Package marker and version
apps/desktop/auraaudio/transcription/contracts.py
                                                  # Engine-neutral notes, request, result, protocol
apps/desktop/auraaudio/transcription/registry.py  # Model metadata and checksum validation
apps/desktop/auraaudio/transcription/basic_pitch_engine.py
                                                  # Basic Pitch ONNX adapter
apps/desktop/auraaudio/transcription/bytedance_engine.py
                                                  # Piano Transcription Inference CPU adapter
apps/desktop/auraaudio/benchmark/schema.py        # Dataset/run/report models
apps/desktop/auraaudio/benchmark/manifest.py      # Manifest loading and validation
apps/desktop/auraaudio/benchmark/audio.py         # ffprobe/FFmpeg normalization and cropping
apps/desktop/auraaudio/benchmark/references.py    # MIDI crop and reference-note conversion
apps/desktop/auraaudio/benchmark/metrics.py       # Note and frame metrics
apps/desktop/auraaudio/benchmark/worker.py        # One engine/clip subprocess entrypoint
apps/desktop/auraaudio/benchmark/runner.py        # Matrix execution and resource measurement
apps/desktop/auraaudio/benchmark/report.py        # Aggregate tables and decision logic
apps/desktop/auraaudio/benchmark/cli.py           # Typer commands
benchmarks/manifests/schema-v1.json               # Exported JSON Schema
benchmarks/manifests/example.json                 # Synthetic/example manifest, no private paths
benchmarks/models/registry.json                   # Model URLs, license, version, size, SHA-256
benchmarks/review/rubric.md                       # Human correction protocol
benchmarks/review/template.csv                    # Ten-clip review form
scripts/benchmark/fetch_models.py                 # Explicit model acquisition and hash verification
scripts/benchmark/prepare_dataset.py              # Prepares local audio/reference fixtures
scripts/benchmark/run_benchmark.py                # Stable CLI wrapper
tests/unit/                                      # Pure fast tests
tests/integration/                               # FFmpeg and optional-model tests
tests/fixtures/                                  # Tiny generated WAV/MIDI pairs only
docs/decisions/0001-transcription-engine.md       # Final go/narrow/stop record
```

---

### Task 1: Scaffold reproducible benchmark package

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `README.md`
- Create: `apps/desktop/auraaudio/__init__.py`
- Create: `apps/desktop/auraaudio/benchmark/__init__.py`
- Create: `apps/desktop/auraaudio/transcription/__init__.py`
- Create: `tests/unit/test_package.py`
- Create: `uv.lock`

**Interfaces:**
- Produces: importable `auraaudio` package with `__version__ == "0.1.0"`
- Produces: console command `aura-benchmark`

- [ ] **Step 1: Install prerequisites**

Install Python 3.11, uv, FFmpeg, and Git. Verify:

```bash
python3.11 --version
uv --version
ffmpeg -version
ffprobe -version
git --version
```

Expected: every command exits `0`; Python reports `3.11.x`.

- [ ] **Step 2: Write failing package test**

```python
# tests/unit/test_package.py
import auraaudio


def test_package_version() -> None:
    assert auraaudio.__version__ == "0.1.0"
```

- [ ] **Step 3: Run test and verify failure**

Run:

```bash
uv run pytest tests/unit/test_package.py -v
```

Expected: FAIL because package or project configuration does not exist.

- [ ] **Step 4: Create project configuration**

Use this `pyproject.toml`:

```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "auraaudio"
version = "0.1.0"
requires-python = ">=3.11,<3.12"
dependencies = [
  "mir-eval>=0.8,<1",
  "numpy>=1.26,<3",
  "pretty-midi>=0.2.10,<1",
  "psutil>=6,<8",
  "pydantic>=2.11,<3",
  "soundfile>=0.13,<1",
  "typer>=0.16,<1",
]

[project.optional-dependencies]
basic-pitch = ["basic-pitch[onnx]==0.4.0"]
bytedance = [
  "piano-transcription-inference==0.0.6",
  "torch>=2.7,<3",
]
dev = [
  "hypothesis>=6.130,<7",
  "pytest>=8.3,<9",
  "pytest-cov>=6,<8",
  "ruff>=0.12,<1",
]

[project.scripts]
aura-benchmark = "auraaudio.benchmark.cli:app"

[tool.hatch.build.targets.wheel]
packages = ["apps/desktop/auraaudio"]

[tool.pytest.ini_options]
addopts = "-ra --strict-markers"
testpaths = ["tests"]
markers = [
  "integration: requires local executables or large model artifacts",
  "model: runs real ML inference",
]

[tool.ruff]
line-length = 100
target-version = "py311"
```

Create package version:

```python
# apps/desktop/auraaudio/__init__.py
__version__ = "0.1.0"
```

Add package marker files with docstrings only. Add `.gitignore` entries:

```gitignore
.venv/
__pycache__/
.pytest_cache/
.ruff_cache/
.coverage
dist/
build/
benchmarks/private/
benchmarks/models/*.onnx
benchmarks/models/*.pth
benchmarks/runs/
benchmarks/reports/generated/
```

- [ ] **Step 5: Lock dependencies and run quality checks**

```bash
uv lock
uv sync --extra dev
uv run pytest tests/unit/test_package.py -v
uv run ruff check .
```

Expected: test passes; Ruff exits `0`; `uv.lock` exists.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore README.md apps tests/unit/test_package.py
git commit -m "chore: scaffold transcription benchmark"
```

---

### Task 2: Define engine-neutral transcription contracts

**Files:**
- Create: `apps/desktop/auraaudio/transcription/contracts.py`
- Create: `tests/unit/transcription/test_contracts.py`

**Interfaces:**
- Produces: `PerformedNote(id: str, pitch: int, onset_seconds: float, offset_seconds: float, velocity: int, confidence: float)`
- Produces: `TranscriptionRequest(audio_path: Path, model_path: Path, engine_id: str)`
- Produces: `TranscriptionResult(engine_id: str, model_sha256: str, notes: tuple[PerformedNote, ...], runtime_seconds: float)`
- Produces: `TranscriptionEngine.transcribe(request, progress, cancelled) -> TranscriptionResult`

- [ ] **Step 1: Write failing validation tests**

```python
# tests/unit/transcription/test_contracts.py
from pathlib import Path

import pytest

from auraaudio.transcription.contracts import PerformedNote, TranscriptionRequest


def test_performed_note_rejects_reversed_time() -> None:
    with pytest.raises(ValueError, match="offset_seconds"):
        PerformedNote("n1", 60, 1.0, 0.5, 80, 0.9)


def test_performed_note_rejects_non_piano_pitch() -> None:
    with pytest.raises(ValueError, match="pitch"):
        PerformedNote("n1", 20, 0.0, 1.0, 80, 0.9)


def test_request_requires_absolute_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="absolute"):
        TranscriptionRequest(Path("audio.wav"), tmp_path / "model.onnx", "basic-pitch-onnx")
```

- [ ] **Step 2: Verify tests fail**

```bash
uv run pytest tests/unit/transcription/test_contracts.py -v
```

Expected: FAIL with import error for `contracts`.

- [ ] **Step 3: Implement immutable contracts**

```python
# apps/desktop/auraaudio/transcription/contracts.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol


@dataclass(frozen=True, slots=True)
class PerformedNote:
    id: str
    pitch: int
    onset_seconds: float
    offset_seconds: float
    velocity: int
    confidence: float

    def __post_init__(self) -> None:
        if not 21 <= self.pitch <= 108:
            raise ValueError("pitch must be within piano range 21..108")
        if self.onset_seconds < 0 or self.offset_seconds <= self.onset_seconds:
            raise ValueError("offset_seconds must be greater than non-negative onset_seconds")
        if not 0 <= self.velocity <= 127:
            raise ValueError("velocity must be within 0..127")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be within 0..1")


@dataclass(frozen=True, slots=True)
class TranscriptionRequest:
    audio_path: Path
    model_path: Path
    engine_id: str

    def __post_init__(self) -> None:
        if not self.audio_path.is_absolute() or not self.model_path.is_absolute():
            raise ValueError("audio_path and model_path must be absolute")


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    engine_id: str
    model_sha256: str
    notes: tuple[PerformedNote, ...]
    runtime_seconds: float


class TranscriptionEngine(Protocol):
    engine_id: str

    def transcribe(
        self,
        request: TranscriptionRequest,
        progress: Callable[[float], None],
        cancelled: Callable[[], bool],
    ) -> TranscriptionResult: ...
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/transcription/test_contracts.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/auraaudio/transcription/contracts.py tests/unit/transcription
git commit -m "feat: define transcription engine contracts"
```

---

### Task 3: Validate 40-clip rights and cohort manifest

**Files:**
- Create: `apps/desktop/auraaudio/benchmark/schema.py`
- Create: `apps/desktop/auraaudio/benchmark/manifest.py`
- Create: `benchmarks/manifests/schema-v1.json`
- Create: `benchmarks/manifests/example.json`
- Create: `tests/unit/benchmark/test_manifest.py`

**Interfaces:**
- Produces: `ClipRecord`, `DatasetManifest`, `load_manifest(path) -> DatasetManifest`
- Produces: `validate_benchmark_manifest(manifest) -> None`
- Consumes later: prepared absolute audio/reference paths and SHA-256 values

- [ ] **Step 1: Write failing cohort and rights tests**

```python
# tests/unit/benchmark/test_manifest.py
from pathlib import Path

import pytest

from auraaudio.benchmark.manifest import load_manifest


def test_manifest_rejects_missing_rights_evidence(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(
        '{"schema_version":1,"dataset_id":"x","clips":['
        '{"clip_id":"c1","cohort":"clean","audio_path":"/a.wav",'
        '"reference_midi_path":"/a.mid","duration_seconds":30,'
        '"license_id":"private","rights_evidence":""}]}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="rights_evidence"):
        load_manifest(path)


def test_release_manifest_requires_ten_clips_per_cohort(valid_39_clip_manifest: Path) -> None:
    with pytest.raises(ValueError, match="exactly 10"):
        load_manifest(valid_39_clip_manifest, release=True)
```

- [ ] **Step 2: Verify tests fail**

```bash
uv run pytest tests/unit/benchmark/test_manifest.py -v
```

Expected: FAIL because manifest models do not exist.

- [ ] **Step 3: Implement manifest models**

Use Pydantic models with these exact fields:

```python
class ClipRecord(BaseModel):
    clip_id: str
    cohort: Literal["clean", "noisy", "room", "device"]
    audio_path: Path
    reference_midi_path: Path
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0, le=300)
    license_id: str = Field(min_length=1)
    rights_evidence: str = Field(min_length=1)
    audio_sha256: str | None = None
    reference_sha256: str | None = None


class DatasetManifest(BaseModel):
    schema_version: Literal[1]
    dataset_id: str
    clips: list[ClipRecord]
```

`load_manifest(path, release=False)` must resolve relative paths against the manifest directory. With `release=True`, require exactly 40 unique IDs, exactly 10 clips in each cohort, at least four clips of 300 seconds, existing files, and 64-character lowercase SHA-256 values.

- [ ] **Step 4: Assemble local rights-cleared source set**

Create `benchmarks/private/source.json` with exactly 40 aligned audio/MIDI pairs: 10 `clean`, 10 `noisy`, 10 `room`, and 10 `device`. At least four entries must use `duration_seconds: 300`; remaining entries must use 20–120 seconds. Each entry records `license_id` and a concrete `rights_evidence` value such as a receipt/license document path, creator release path, or `recorded and owned by <reviewer-id>`. Do not use streamed, DRM-protected, or unclear-rights recordings. Keep media and evidence files outside Git.

For MAESTRO material, record `license_id: CC-BY-NC-SA-4.0`, dataset version `3.0.0` in `rights_evidence`, and use it only when non-commercial terms fit this benchmark. Do not redistribute cropped audio.

- [ ] **Step 5: Export schema and example**

Add a CLI function that writes `DatasetManifest.model_json_schema()` to `benchmarks/manifests/schema-v1.json`. The committed example contains four synthetic entries, one per cohort, and explicitly sets `rights_evidence` to `generated test fixture; no third-party recording`.

- [ ] **Step 6: Run tests and schema generation**

```bash
uv run pytest tests/unit/benchmark/test_manifest.py -v
uv run python -m auraaudio.benchmark.manifest export-schema benchmarks/manifests/schema-v1.json
git diff --check
```

Expected: tests pass; schema file is stable on a second run.

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/auraaudio/benchmark benchmarks/manifests tests/unit/benchmark
git commit -m "feat: validate benchmark dataset manifest"
```

---

### Task 4: Prepare deterministic audio and MIDI references

**Files:**
- Create: `apps/desktop/auraaudio/benchmark/audio.py`
- Create: `apps/desktop/auraaudio/benchmark/references.py`
- Create: `scripts/benchmark/prepare_dataset.py`
- Create: `tests/fixtures/generate_fixture.py`
- Create: `tests/integration/benchmark/test_prepare_dataset.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `probe_audio(path: Path) -> AudioProbe`
- Produces: `normalize_audio(source, destination, start_seconds, duration_seconds) -> None`
- Produces: `crop_reference_midi(source, destination, start_seconds, duration_seconds) -> None`
- Produces: prepared release manifest with hashes

- [ ] **Step 1: Generate tiny test fixture**

Create a 4-second, 44.1 kHz stereo WAV containing MIDI pitches 60 and 64 as sine waves and a matching MIDI with notes `[0.5, 1.5]` and `[2.0, 3.0]`. Generated fixtures contain no copyrighted performance.

- [ ] **Step 2: Write failing integration test**

```python
def test_prepare_dataset_normalizes_and_aligns(tmp_path: Path, generated_pair: AudioMidiPair) -> None:
    prepared = prepare_clip(
        generated_pair.audio,
        generated_pair.midi,
        tmp_path,
        start_seconds=1.0,
        duration_seconds=2.0,
    )
    assert prepared.sample_rate == 16000
    assert prepared.channels == 1
    notes = load_reference_notes(prepared.reference_midi_path)
    assert [(n.pitch, n.onset_seconds, n.offset_seconds) for n in notes] == [
        (60, 0.0, 0.5),
        (64, 1.0, 2.0),
    ]
```

- [ ] **Step 3: Verify failure**

```bash
uv run pytest tests/integration/benchmark/test_prepare_dataset.py -v -m integration
```

Expected: FAIL because preparation functions do not exist.

- [ ] **Step 4: Implement safe FFmpeg invocation**

Invoke subprocesses with argument arrays and `shell=False`. Normalization command must be equivalent to:

```bash
ffmpeg -nostdin -hide_banner -loglevel error -ss START -t DURATION \
  -i SOURCE -map 0:a:0 -vn -ac 1 -ar 16000 -sample_fmt s16 \
  -af loudnorm=I=-23:LRA=7:TP=-2 DESTINATION
```

Reject sources with no audio stream, duration over 300 seconds after cropping, non-finite probe values, or unexpected output properties. Write to `*.tmp.wav`, verify through ffprobe, then atomically rename.

- [ ] **Step 5: Implement MIDI crop**

Use `pretty_midi` to shift events by `start_seconds`, clamp notes crossing crop edges, discard events outside the crop, preserve pitch and velocity, and write one piano instrument. Validate every output note through `PerformedNote`.

- [ ] **Step 6: Implement preparation CLI**

```bash
uv run python scripts/benchmark/prepare_dataset.py \
  --source-manifest benchmarks/private/source.json \
  --output-root benchmarks/private/prepared \
  --output-manifest benchmarks/private/prepared.json
```

The command must refuse to overwrite a prepared clip when its recorded source hashes differ. It must calculate audio/reference SHA-256 and emit a manifest that passes `load_manifest(..., release=True)`.

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/integration/benchmark/test_prepare_dataset.py -v -m integration
uv run pytest tests/unit -q
```

Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add apps/desktop/auraaudio/benchmark scripts/benchmark tests .gitignore
git commit -m "feat: prepare aligned benchmark fixtures"
```

---

### Task 5: Pin model artifacts and block implicit downloads

**Files:**
- Create: `apps/desktop/auraaudio/transcription/registry.py`
- Create: `benchmarks/models/registry.json`
- Create: `scripts/benchmark/fetch_models.py`
- Create: `tests/unit/transcription/test_registry.py`

**Interfaces:**
- Produces: `ModelRecord(engine_id, filename, source_url, sha256: str | None, sha256_source: str | None, license_id, license_url)`
- Produces: `load_model_registry(path)`, `verify_model(record, root) -> Path`
- Consumes later: verified absolute model paths for both adapters

- [ ] **Step 1: Write failing checksum test**

```python
def test_verify_model_rejects_hash_mismatch(tmp_path: Path) -> None:
    model = tmp_path / "model.bin"
    model.write_bytes(b"wrong")
    record = ModelRecord(
        engine_id="example",
        filename="model.bin",
        source_url="https://example.invalid/model.bin",
        sha256="0" * 64,
        license_id="MIT",
        license_url="https://example.invalid/license",
    )
    with pytest.raises(ValueError, match="SHA-256"):
        verify_model(record, tmp_path)
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/transcription/test_registry.py -v
```

- [ ] **Step 3: Implement registry and fetch command**

`fetch_models.py` downloads only when explicitly run, writes to a temporary file, checks HTTPS final URL, validates SHA-256, then atomically renames. Runtime adapters never download. An unresolved package-owned record may have `sha256=null` plus `sha256_source`; `verify_model` rejects unresolved records, while `fetch_models.py` resolves and writes an exact hash before any adapter can run.

Registry entries:

```json
{
  "schema_version": 1,
  "models": [
    {
      "engine_id": "basic-pitch-onnx",
      "filename": "basic-pitch-0.4.0.nmp.onnx",
      "source_url": "package:basic_pitch/saved_models/icassp_2022/nmp.onnx",
      "sha256": null,
      "sha256_source": "computed from locked basic-pitch 0.4.0 package during fetch-models",
      "license_id": "Apache-2.0",
      "license_url": "https://github.com/spotify/basic-pitch/blob/main/LICENSE"
    },
    {
      "engine_id": "bytedance-high-resolution-cpu",
      "filename": "CRNN_note_F1=0.9677_pedal_F1=0.9186.pth",
      "source_url": "https://zenodo.org/record/4034264/files/CRNN_note_F1%3D0.9677_pedal_F1%3D0.9186.pth?download=1",
      "sha256": "c3fa9730725bf4a762f1c14bc80cd5986eacda01b026f5a4a2525cd607876141",
      "license_id": "MIT",
      "license_url": "https://github.com/qiuqiangkong/piano_transcription_inference"
    }
  ]
}
```

For the package-owned Basic Pitch model, `fetch_models.py` copies the installed file, computes SHA-256, replaces `sha256_source` with exact `sha256`, and writes a local resolved registry under `benchmarks/private/models/registry.resolved.json`. Do not modify committed registry with machine-specific paths.

- [ ] **Step 4: Run tests and fetch models**

```bash
uv sync --extra dev --extra basic-pitch --extra bytedance
uv run pytest tests/unit/transcription/test_registry.py -v
uv run python scripts/benchmark/fetch_models.py \
  --registry benchmarks/models/registry.json \
  --output benchmarks/private/models
```

Expected: both files exist, hashes validate, resolved registry contains two exact SHA-256 values. If upstream bytes do not match the pinned ByteDance hash, stop; do not run pickle-based `torch.load`.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/auraaudio/transcription/registry.py benchmarks/models/registry.json scripts/benchmark/fetch_models.py tests/unit/transcription/test_registry.py
git commit -m "feat: verify transcription model artifacts"
```

---

### Task 6: Implement Basic Pitch ONNX adapter

**Files:**
- Create: `apps/desktop/auraaudio/transcription/basic_pitch_engine.py`
- Create: `tests/unit/transcription/test_basic_pitch_engine.py`
- Create: `tests/integration/transcription/test_basic_pitch_engine.py`

**Interfaces:**
- Consumes: `TranscriptionRequest`, verified `nmp.onnx`
- Produces: `BasicPitchEngine.transcribe(...) -> TranscriptionResult`

- [ ] **Step 1: Write failing conversion test**

```python
def test_converts_basic_pitch_events_to_contract(monkeypatch, request: TranscriptionRequest) -> None:
    monkeypatch.setattr(
        "auraaudio.transcription.basic_pitch_engine.predict",
        lambda *_args, **_kwargs: ({}, object(), [(0.1, 0.6, 60, 0.5, None)]),
    )
    result = BasicPitchEngine().transcribe(request, lambda _: None, lambda: False)
    assert [(n.pitch, n.velocity, n.confidence) for n in result.notes] == [(60, 64, 0.5)]
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/transcription/test_basic_pitch_engine.py -v
```

- [ ] **Step 3: Implement adapter**

Call `basic_pitch.inference.predict(audio_path, model_path)` with the verified ONNX path. Convert amplitude to velocity with `round(amplitude * 127)`, clamp to `0..127`, use amplitude as confidence, sort by `(onset_seconds, pitch, offset_seconds)`, and create deterministic IDs `bp-000000`, `bp-000001`, and so on. Report progress `0.0` before inference and `1.0` after conversion. Check cancellation before loading and after inference.

- [ ] **Step 4: Add real-model smoke test**

Mark the test `integration` and `model`. Run the generated 4-second fixture, assert at least one note, all notes satisfy contracts, engine ID equals `basic-pitch-onnx`, and model hash matches registry.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/transcription/test_basic_pitch_engine.py -v
uv run pytest tests/integration/transcription/test_basic_pitch_engine.py -v -m "integration and model"
```

Expected: both pass using `CPUExecutionProvider` only.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/auraaudio/transcription/basic_pitch_engine.py tests
git commit -m "feat: add Basic Pitch ONNX adapter"
```

---

### Task 7: Implement high-resolution piano CPU adapter

**Files:**
- Create: `apps/desktop/auraaudio/transcription/bytedance_engine.py`
- Create: `tests/unit/transcription/test_bytedance_engine.py`
- Create: `tests/integration/transcription/test_bytedance_engine.py`

**Interfaces:**
- Consumes: `TranscriptionRequest`, verified `.pth` checkpoint, mono 16 kHz WAV
- Produces: `ByteDancePianoEngine.transcribe(...) -> TranscriptionResult`

- [ ] **Step 1: Write failing event-conversion test**

```python
def test_converts_bytedance_events_to_contract(monkeypatch, request: TranscriptionRequest) -> None:
    fake = {"est_note_events": [
        {"onset_time": 0.1, "offset_time": 0.6, "midi_note": 60, "velocity": 83}
    ]}
    monkeypatch.setattr(FakeTranscriptor, "transcribe", lambda *_args, **_kwargs: fake)
    result = make_engine(FakeTranscriptor).transcribe(request, lambda _: None, lambda: False)
    assert [(n.pitch, n.velocity) for n in result.notes] == [(60, 83)]
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/transcription/test_bytedance_engine.py -v
```

- [ ] **Step 3: Implement explicit CPU adapter**

Load audio through `soundfile`, require 16 kHz mono input, and construct:

```python
PianoTranscription(
    device="cpu",
    checkpoint_path=str(request.model_path),
)
```

Always pass `checkpoint_path`; never allow upstream automatic `wget`. Call `transcribe(audio, midi_path=None)`. Convert event dictionaries to `PerformedNote`. Upstream does not expose per-note confidence, so set `confidence=1.0` and record `confidence_available=false` in engine metadata. Use deterministic IDs `bd-000000`, `bd-000001`, and so on.

- [ ] **Step 4: Guard unsafe or incompatible checkpoints**

Verify SHA-256 before importing the checkpoint. Call `torch.load` only through the pinned upstream class after hash validation. If current PyTorch rejects or changes checkpoint loading semantics, document the exact exception in the benchmark report and mark this engine `incompatible`; do not disable safety checks globally.

- [ ] **Step 5: Add real-model smoke test**

Run the generated 4-second fixture on CPU. Assert completion, valid notes, correct engine/model IDs, and no network socket attempt. Patch `socket.socket.connect` to raise during the test so an implicit download fails decisively.

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/unit/transcription/test_bytedance_engine.py -v
uv run pytest tests/integration/transcription/test_bytedance_engine.py -v -m "integration and model"
```

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/auraaudio/transcription/bytedance_engine.py tests
git commit -m "feat: add piano transcription CPU adapter"
```

---

### Task 8: Implement objective transcription metrics

**Files:**
- Create: `apps/desktop/auraaudio/benchmark/metrics.py`
- Create: `tests/unit/benchmark/test_metrics.py`

**Interfaces:**
- Produces: `MetricResult(onset_precision, onset_recall, onset_f1, onset_offset_precision, onset_offset_recall, onset_offset_f1, frame_precision, frame_recall, frame_f1)`
- Produces: `evaluate_notes(reference, estimate, duration_seconds) -> MetricResult`

- [ ] **Step 1: Write exact metric tests**

```python
def test_perfect_notes_score_one() -> None:
    notes = (PerformedNote("n", 60, 0.0, 1.0, 80, 1.0),)
    result = evaluate_notes(notes, notes, duration_seconds=1.0)
    assert result.onset_f1 == pytest.approx(1.0)
    assert result.onset_offset_f1 == pytest.approx(1.0)
    assert result.frame_f1 == pytest.approx(1.0)


def test_wrong_pitch_scores_zero() -> None:
    ref = (PerformedNote("r", 60, 0.0, 1.0, 80, 1.0),)
    est = (PerformedNote("e", 61, 0.0, 1.0, 80, 1.0),)
    result = evaluate_notes(ref, est, duration_seconds=1.0)
    assert result.onset_f1 == 0.0
    assert result.onset_offset_f1 == 0.0
    assert result.frame_f1 == 0.0
```

- [ ] **Step 2: Add property tests**

Use Hypothesis to prove every precision, recall, and F1 value stays within `0.0..1.0`, empty/empty returns `1.0`, and swapping reference/estimate swaps precision/recall while preserving F1.

- [ ] **Step 3: Verify failure**

```bash
uv run pytest tests/unit/benchmark/test_metrics.py -v
```

- [ ] **Step 4: Implement metrics**

For note metrics, convert MIDI pitch to Hz with `440.0 * 2 ** ((pitch - 69) / 12)` and call `mir_eval.transcription.precision_recall_f1_overlap` twice:

- onset-only: `offset_ratio=None`, `onset_tolerance=0.05`, `pitch_tolerance=50.0`;
- onset-plus-offset: `offset_ratio=0.2`, `offset_min_tolerance=0.05`, same onset/pitch tolerances.

For frame F1, create 10 ms piano-roll occupancy matrices with shape `(ceil(duration / 0.01), 88)` for pitches 21–108 and compute micro precision/recall/F1 over booleans.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/unit/benchmark/test_metrics.py -v
```

Expected: all deterministic and property tests pass.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/auraaudio/benchmark/metrics.py tests/unit/benchmark/test_metrics.py
git commit -m "feat: measure transcription quality"
```

---

### Task 9: Run each benchmark job in a measured subprocess

**Files:**
- Create: `apps/desktop/auraaudio/benchmark/worker.py`
- Create: `apps/desktop/auraaudio/benchmark/runner.py`
- Create: `apps/desktop/auraaudio/benchmark/cli.py`
- Create: `scripts/benchmark/run_benchmark.py`
- Create: `tests/unit/benchmark/test_runner.py`
- Create: `tests/integration/benchmark/test_worker.py`

**Interfaces:**
- Produces: `RunRecord` JSON for one `(engine, clip, repetition)`
- Produces: JSONL run ledger under `benchmarks/runs/<run-id>/runs.jsonl`
- Consumes: release manifest, resolved model registry, engine adapters, metrics

- [ ] **Step 1: Write failing resume test**

```python
def test_runner_skips_completed_matching_job(tmp_path: Path, fake_job: BenchmarkJob) -> None:
    ledger = RunLedger(tmp_path / "runs.jsonl")
    ledger.append(success_record_for(fake_job))
    executed = run_jobs([fake_job], ledger, execute=lambda _: pytest.fail("must skip"))
    assert executed == []
```

- [ ] **Step 2: Write failing memory-limit test**

Create a fake worker that allocates beyond a 64 MB test limit. Assert runner terminates it and records `status="memory_limit"` rather than crashing the benchmark.

- [ ] **Step 3: Verify failure**

```bash
uv run pytest tests/unit/benchmark/test_runner.py -v
```

- [ ] **Step 4: Implement worker protocol**

Worker input is one JSON document on stdin; output is one JSON document on stdout. Logs go to stderr. Worker verifies input/model hashes, builds requested adapter, transcribes, calculates metrics, writes prediction JSON atomically, and returns:

```json
{
  "schema_version": 1,
  "job_id": "sha256-of-engine-model-clip-config-repetition",
  "status": "succeeded",
  "engine_id": "basic-pitch-onnx",
  "clip_id": "clip-001",
  "runtime_seconds": 12.34,
  "audio_seconds": 30.0,
  "real_time_factor": 0.4113,
  "peak_rss_bytes": 123456789,
  "metrics": {
    "onset_f1": 0.82,
    "onset_offset_f1": 0.64,
    "frame_f1": 0.71
  }
}
```

Failures use `status="failed"` and a stable error code; they do not include private absolute paths.

- [ ] **Step 5: Implement process supervisor**

Use `subprocess.Popen` with argument arrays. Poll process plus recursive children through psutil every 100 ms. Track combined peak RSS. Terminate at 12 GB or at timeout `max(120 seconds, audio duration * 5)`. Send terminate, wait 10 seconds, then kill if required. Append each result to JSONL followed by flush and `os.fsync`.

- [ ] **Step 6: Implement execution matrix**

Run both engines on all 40 clips once for quality. Run two additional repetitions for the eight performance clips: four 300-second clips and four clips selected by sorted SHA-256. Warm each engine once on the generated 4-second fixture before recording. Randomization uses seed `20260814` and stores final job order.

- [ ] **Step 7: Implement CLI**

```bash
uv run aura-benchmark run \
  --manifest benchmarks/private/prepared.json \
  --models benchmarks/private/models/registry.resolved.json \
  --output benchmarks/runs/phase0-reference \
  --seed 20260814
```

`--resume` is default. `--restart` requires an empty new output directory; it never deletes existing runs.

- [ ] **Step 8: Run tests**

```bash
uv run pytest tests/unit/benchmark/test_runner.py -v
uv run pytest tests/integration/benchmark/test_worker.py -v -m integration
```

- [ ] **Step 9: Commit**

```bash
git add apps/desktop/auraaudio/benchmark scripts/benchmark tests
git commit -m "feat: run measured transcription benchmarks"
```

---

### Task 10: Add human correction-effort review

**Files:**
- Create: `benchmarks/review/rubric.md`
- Create: `benchmarks/review/template.csv`
- Create: `apps/desktop/auraaudio/benchmark/review.py`
- Create: `tests/unit/benchmark/test_review.py`

**Interfaces:**
- Produces: validated `ReviewRecord`
- Produces: ten-clip review aggregate per engine
- Consumes: five clean and five degraded clips selected by sorted clip SHA-256

- [ ] **Step 1: Define rubric**

For each engine/clip, reviewer listens to source, compares predicted MIDI in MuseScore, corrects pitch/onset/offset only, and records:

- `engine_id`
- `clip_id`
- `reviewer_id`
- `started_at_utc`
- `finished_at_utc`
- `notes_added`
- `notes_deleted`
- `notes_pitch_changed`
- `notes_timing_changed`
- `unusable` boolean
- `comment` limited to 500 characters

Reviewer sees anonymized engine labels A/B in randomized order. Review one 30–60 second excerpt per selected clip, not all 5 minutes. Stop timer during breaks.

- [ ] **Step 2: Write failing validator tests**

Assert negative edit counts, finish before start, unknown clip, unknown engine, duplicate reviewer/engine/clip, and comments over 500 characters are rejected.

- [ ] **Step 3: Implement validation and aggregation**

Calculate correction seconds, correction-time ratio, total touched notes, touched-note ratio, and unusable rate. Store raw local review CSV under `benchmarks/private/reviews/`; commit only the empty template and rubric.

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/unit/benchmark/test_review.py -v
```

- [ ] **Step 5: Conduct blinded review**

```bash
uv run aura-benchmark prepare-review \
  --run benchmarks/runs/phase0-reference \
  --output benchmarks/private/reviews/phase0
uv run aura-benchmark validate-review \
  benchmarks/private/reviews/phase0/completed.csv
```

Expected: exactly 20 review rows: 10 clips multiplied by two engines.

- [ ] **Step 6: Commit**

```bash
git add benchmarks/review apps/desktop/auraaudio/benchmark/review.py tests/unit/benchmark/test_review.py
git commit -m "feat: measure transcription correction effort"
```

---

### Task 11: Generate benchmark report and decision record

**Files:**
- Create: `apps/desktop/auraaudio/benchmark/report.py`
- Create: `tests/unit/benchmark/test_report.py`
- Create: `docs/decisions/0001-transcription-engine.md`
- Modify: `README.md`

**Interfaces:**
- Produces: `Decision(outcome: Literal["go", "narrow", "stop"], selected_engine_id: str | None, failed_gates: tuple[str, ...])`
- Produces: local `summary.json`, `summary.csv`, and `report.md`

- [ ] **Step 1: Write failing gate tests**

```python
def test_selects_highest_quality_passing_engine() -> None:
    decision = decide([passing_summary("a", onset_f1=0.81), passing_summary("b", onset_f1=0.84)])
    assert decision.outcome == "go"
    assert decision.selected_engine_id == "b"


def test_narrows_when_only_clean_cohort_passes() -> None:
    decision = decide([summary_that_passes_clean_and_fails_degraded("a")])
    assert decision.outcome == "narrow"
    assert decision.selected_engine_id == "a"


def test_stops_when_no_engine_passes_clean_gate() -> None:
    decision = decide([failing_summary("a"), failing_summary("b")])
    assert decision.outcome == "stop"
    assert decision.selected_engine_id is None
```

- [ ] **Step 2: Verify failure**

```bash
uv run pytest tests/unit/benchmark/test_report.py -v
```

- [ ] **Step 3: Implement aggregation**

Report per engine and cohort: clip count, completion rate, median and quartiles for each quality metric, runtime median/p95, real-time factor median/p95, peak RSS maximum, installed dependency size, correction-time ratio median, touched-note ratio median, and unusable rate. List failures by stable error code without private paths.

- [ ] **Step 4: Implement exact decision rules**

1. Exclude an engine failing completion, memory, p95 real-time factor, clean onset F1, clean onset-plus-offset F1, or correction-time gate.
2. If at least one engine also passes degraded onset F1, outcome is `go` and selection follows quality/runtime/size tie-breakers.
3. If an engine passes every gate except degraded onset F1, outcome is `narrow`; initial product supports clean close-miked solo-piano recordings only.
4. If no engine passes clean gates, outcome is `stop`; do not start desktop product implementation.

- [ ] **Step 5: Generate local report**

```bash
uv run aura-benchmark report \
  --run benchmarks/runs/phase0-reference \
  --review benchmarks/private/reviews/phase0/completed.csv \
  --output benchmarks/reports/generated/phase0-reference
```

Expected: command exits `0` for `go` or `narrow`, exits `2` for `stop`, and writes `summary.json`, `summary.csv`, and `report.md`.

- [ ] **Step 6: Write decision record from generated evidence**

Populate `docs/decisions/0001-transcription-engine.md` with:

- date and reference hardware;
- dataset ID and manifest SHA-256;
- exact package/model/config hashes;
- per-cohort aggregate table;
- selected outcome and engine;
- failed gates and known failure cohorts;
- desktop MVP input promise;
- rollback/fallback engine status;
- links to local report paths without private filenames.

The decision record must contain measured values, not estimates. Do not commit raw private recordings or absolute paths.

- [ ] **Step 7: Run full verification**

```bash
uv run pytest -m "not model" --cov=auraaudio --cov-report=term-missing
uv run pytest -m model tests/integration/transcription -v
uv run ruff check .
uv run aura-benchmark validate-manifest benchmarks/private/prepared.json --release
uv run aura-benchmark verify-run benchmarks/runs/phase0-reference
git diff --check
```

Expected: all tests pass; manifest has 40 clips; every planned job has one terminal record; no unverified model or input hash exists.

- [ ] **Step 8: Commit**

```bash
git add apps/desktop/auraaudio/benchmark/report.py tests/unit/benchmark/test_report.py docs/decisions/0001-transcription-engine.md README.md
git commit -m "docs: record transcription engine decision"
```

---

### Task 12: Cross-platform reproducibility gate

**Files:**
- Create: `.github/workflows/phase0-smoke.yml`
- Create: `scripts/benchmark/environment_report.py`
- Create: `tests/integration/benchmark/test_offline_smoke.py`
- Modify: `docs/decisions/0001-transcription-engine.md`

**Interfaces:**
- Produces: sanitized environment JSON for Windows and macOS
- Confirms: selected engine installs and transcribes generated fixture offline on both platforms

- [ ] **Step 1: Write offline smoke test**

Patch DNS/socket connection functions to fail, invoke selected engine on generated fixture, and assert a valid `TranscriptionResult`. Skip only when verified model artifact is absent; CI setup must provide it from a checksum-addressed private artifact.

- [ ] **Step 2: Create cross-platform workflow**

Use a matrix of `windows-latest` and `macos-14`, Python 3.11, and selected-engine dependency extra. Workflow generates its own 4-second WAV/MIDI fixture, restores the verified model by SHA-256 key, disables network for test process, runs the smoke test, and uploads sanitized environment/result JSON. Never upload third-party benchmark audio.

- [ ] **Step 3: Record environment**

`environment_report.py` emits OS version, architecture, logical/physical CPU count, total RAM, Python version, dependency lock hash, model hash, runtime, peak RSS, and result note count. It excludes username, hostname, home directory, and absolute paths.

- [ ] **Step 4: Run local equivalent on both reference machines**

```bash
uv sync --extra dev --extra basic-pitch --extra bytedance
uv run pytest tests/integration/benchmark/test_offline_smoke.py -v -m "integration and model"
uv run python scripts/benchmark/environment_report.py \
  --output benchmarks/private/environment-$(python -c "import platform; print(platform.system().lower())").json
```

Expected: selected engine succeeds offline on both Windows and macOS, stays under 12 GB, and produces valid notes.

- [ ] **Step 5: Update decision record**

Add cross-platform result table. If either platform fails, change decision to `narrow` for the passing platform or `stop` when neither passes. Do not claim Windows/macOS support without both rows passing.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/phase0-smoke.yml scripts/benchmark/environment_report.py tests/integration/benchmark/test_offline_smoke.py docs/decisions/0001-transcription-engine.md
git commit -m "ci: verify transcription engine platforms"
```

## Completion checklist

- [ ] Exactly 40 rights-cleared clips pass release-manifest validation.
- [ ] Both candidate model artifacts have verified SHA-256 values.
- [ ] Every benchmark job has a terminal, resumable JSONL record.
- [ ] Quality, runtime, memory, size, and correction-effort metrics are present.
- [ ] Windows and macOS offline smoke tests pass for selected engine.
- [ ] Decision record says exactly `go`, `narrow`, or `stop` and includes measured evidence.
- [ ] No private media, references, model weights, absolute paths, or unreviewed raw outputs are staged.
- [ ] Full test and Ruff commands pass immediately before handoff.

## Reference sources

- [Basic Pitch repository and Apache-2.0 license](https://github.com/spotify/basic-pitch)
- [Basic Pitch model-runtime documentation](https://github.com/spotify/basic-pitch#model-runtime)
- [Piano Transcription Inference CPU usage](https://github.com/qiuqiangkong/piano_transcription_inference)
- [High-resolution piano transcription implementation](https://github.com/bytedance/piano_transcription)
- [mir_eval transcription metrics](https://mir-eval.readthedocs.io/latest/api/transcription.html)
- [ONNX Runtime CPU installation](https://onnxruntime.ai/docs/install/)
- [MAESTRO dataset and CC BY-NC-SA 4.0 license](https://magenta.tensorflow.org/datasets/maestro)
