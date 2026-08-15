# Transcription Vertical Slice Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the smallest end-to-end slice of AuraAudio that takes an uploaded short solo audio fixture through an asynchronous job pipeline (upload → probe → normalize → transcribe → quantize → export) and produces a downloadable MIDI file and a minimal, schema-valid MusicXML file, deterministically and without duplicate GPU/CPU work on retry.

**Architecture:** A FastAPI service (`apps/api`) owns projects, jobs, and exports in PostgreSQL and issues signed upload/download URLs against MinIO (S3-compatible object storage). It enqueues an idempotent job on Redis via RQ. A separate worker process (`workers/transcription`) runs the job as a sequence of resumable stages (probe/validate → decode/normalize → inference → quantize → export), persisting a `StageArtifact` per stage so a retried job resumes rather than recomputes. Two shared packages (`packages/score_schema`, `packages/musicxml`) hold the canonical score JSON contract and the MusicXML writer/validator so both services agree on shapes. No web UI, auth, quotas, or PDF export are in this slice — see **Scope** below.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy 2.0 (sync) + Alembic, PostgreSQL, Redis + RQ, boto3 against MinIO, FFmpeg (subprocess), `basic-pitch` (Spotify's open-source pitch/onset model) for inference, `mido` for MIDI, `music21` for MusicXML generation and round-trip validation, `jsonschema` for canonical score validation, pytest + `httpx` for tests, Docker + docker-compose for local infra.

**Spec:** `ARCHITECTURE.md` (repo root) — this plan implements Phase 1 ("vertical slice") of section 10, using the data model of section 5, the endpoint list of section 4.2, and the stage pipeline of section 4.3, scoped down as described below.

## Scope

`ARCHITECTURE.md` describes a full product: web client, API, worker, renderer, security hardening, observability, and a four-phase rollout. Per the writing-plans scope rule, a spec spanning multiple independent subsystems should be split into one plan per subsystem/phase rather than one mega-plan. This plan covers **only Phase 1** as defined in `ARCHITECTURE.md` §10:

- Repo layout, formatting, type checks, tests, container builds, local Postgres/object-store/queue dependencies.
- Direct upload, project creation, one asynchronous job, FFmpeg normalization, baseline inference, event decoding, artifact persistence.
- Downloadable MIDI and minimal MusicXML from a short solo clip.
- Structured stage progress and errors.

Explicitly **out of scope** for this plan (later plans, per `ARCHITECTURE.md` §10 Phases 2-4): web client / SVG score preview, beat & meter estimation, guitar string/fret and piano hand assignment, PDF rendering, semantic edits/undo/redo, auth/quotas/retention, security hardening, and observability dashboards. The exit criterion this plan targets is the Phase 1 exit criterion verbatim: *"a developer can upload a fixed guitar or piano fixture and receive deterministic MIDI/MusicXML twice without duplicate GPU processing; integration tests exercise the flow."*

Because there is no web UI yet, `instrument` is a required field on project creation (`"guitar"` or `"piano"`) but does not yet drive string/fret or hand assignment — quantization treats both instruments identically in this slice (that intelligence is Phase 2).

## Global Constraints

(Copied verbatim from `ARCHITECTURE.md` where noted; apply to every task below.)

- Supported source containers: MP3, WAV, M4A, MP4, MOV. Max duration 15 minutes, max size 500 MB (§1 Assumptions).
- Processing is asynchronous; the API must never proxy large media bytes through the application process — uploads and downloads use signed URLs directly against object storage (§4.2, §7).
- All mutating API requests accept an `Idempotency-Key`; a job conflict returns the existing job rather than scheduling duplicate work (§4.2, §6).
- Job states are `created -> uploaded -> queued -> running -> succeeded|failed|cancelled`; `running` has named stages; state transitions use a database compare-and-set (§6).
- Each stage writes to a temporary object, verifies its checksum, promotes it, then records completion transactionally; a retry resumes from the last valid artifact (§4.3, §6).
- Structured error codes are separate from diagnostic detail. This slice uses: `UNSUPPORTED_MEDIA`, `MEDIA_TOO_LARGE`, `DECODE_FAILED`, `NO_MUSIC_DETECTED`, `MODEL_FAILED`, `EXPORT_FAILED`, `INTERNAL_ERROR` (§6).
- `input_hash` is derived from media SHA-256, crop, instrument settings, and pipeline version (§6).
- Store confidence at the event level in the canonical score; use rational-number strings for notated beats/durations, floats only for seconds (§5).
- FFmpeg and any renderer run as non-root in the container (§7) — enforced in this slice via the worker Dockerfile `USER` directive; sandboxing (no network, read-only rootfs) is deferred to Phase 3 hardening.

---

## File Structure

```text
apps/
  api/
    pyproject.toml
    Dockerfile
    alembic.ini
    alembic/
      env.py
      versions/0001_initial.py
    src/aura_api/
      __init__.py
      main.py            # FastAPI app factory, router registration, health check
      config.py          # pydantic-settings Settings
      db.py               # SQLAlchemy engine/session factory
      models.py           # ORM models: Project, MediaAsset, TranscriptionJob, StageArtifact, ScoreRevision, Export
      schemas.py           # pydantic request/response models
      storage.py           # MinIO/S3 client wrapper, signed URL helpers
      queue.py             # RQ queue wrapper
      idempotency.py        # Idempotency-Key handling
      routers/
        uploads.py
        projects.py
        jobs.py
        exports.py
    tests/
      conftest.py
      test_uploads.py
      test_projects.py
      test_jobs_and_exports.py
      test_idempotency.py

workers/
  transcription/
    pyproject.toml
    Dockerfile
    src/aura_worker/
      __init__.py
      runner.py            # RQ entrypoint: run_transcription_job(job_id)
      stage_runner.py       # StageContext + find_cached_artifact()/save_artifact() resume helpers
      ffmpeg_utils.py        # probe + normalize subprocess wrappers
      stages/
        probe.py
        normalize.py
        inference.py
        quantize.py
        export.py
    tests/
      conftest.py
      test_ffmpeg_utils.py
      test_probe.py
      test_normalize.py
      test_inference.py
      test_quantize.py
      test_export.py
      test_pipeline_e2e.py

packages/
  score_schema/
    pyproject.toml
    src/score_schema/
      __init__.py
      models.py            # NoteEvent, JobErrorCode, score dict builders
      validate.py           # JSON Schema for canonical score, validate_score()
    tests/
      test_models.py
      test_validate.py
  musicxml/
    pyproject.toml
    src/musicxml/
      __init__.py
      export.py             # score_json_to_musicxml()
      validate.py            # reopen-and-check smoke test
    tests/
      test_export.py
      test_validate.py
  test_fixtures/
    pyproject.toml
    src/test_fixtures/
      __init__.py
      generate.py            # synthesizes a short rights-free guitar-pluck WAV
    fixtures/                # generated at test-setup time, not committed as binary

infra/
  docker-compose.yml
  .env.example

docs/
  superpowers/plans/2026-08-15-transcription-vertical-slice.md   # this file
```

**Responsibilities:**

- `packages/score_schema` — the canonical score JSON contract (§5) and the shared `JobErrorCode` enum. Both `apps/api` and `workers/transcription` depend on it so job/error typing never drifts between services.
- `packages/musicxml` — the only place that knows how to turn a canonical score dict into a MusicXML file, and the only place that validates one back.
- `packages/test_fixtures` — generates a tiny, license-free audio fixture at test time (a synthesized guitar-like pluck sequence) so integration tests never depend on a real rights-cleared recording being checked into the repo.
- `apps/api` — HTTP boundary only: never touches FFmpeg, the model, or MusicXML directly.
- `workers/transcription` — all heavy processing; talks to Postgres and object storage directly (not through the API) per §4.3's stage-runner design.

---

## Task 1: Monorepo scaffold and tooling

**Files:**
- Create: `pyproject.toml` (repo root, workspace-level tool config only)
- Create: `apps/api/pyproject.toml`
- Create: `workers/transcription/pyproject.toml`
- Create: `packages/score_schema/pyproject.toml`
- Create: `packages/musicxml/pyproject.toml`
- Create: `packages/test_fixtures/pyproject.toml`
- Create: `.gitignore`
- Create: `Makefile`

**Interfaces:**
- Produces: a `uv`-managed workspace where `apps/api`, `workers/transcription`, and every `packages/*` are installable local packages, and `make test` runs every package's test suite.

- [ ] **Step 1: Write the root workspace config**

```toml
# pyproject.toml
[tool.uv.workspace]
members = [
    "apps/api",
    "workers/transcription",
    "packages/score_schema",
    "packages/musicxml",
    "packages/test_fixtures",
]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = []  # each package defines its own testpaths; root only holds shared config
```

- [ ] **Step 2: Write `packages/score_schema/pyproject.toml`**

```toml
[project]
name = "score-schema"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["jsonschema>=4.23"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/score_schema"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 3: Write `packages/musicxml/pyproject.toml`**

```toml
[project]
name = "musicxml"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["music21>=9.1", "score-schema"]

[tool.uv.sources]
score-schema = { workspace = true }

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/musicxml"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 4: Write `packages/test_fixtures/pyproject.toml`**

```toml
[project]
name = "test-fixtures"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = ["numpy>=1.26", "scipy>=1.13"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/test_fixtures"]
```

- [ ] **Step 5: Write `apps/api/pyproject.toml`**

```toml
[project]
name = "aura-api"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "sqlalchemy>=2.0",
    "psycopg2-binary>=2.9",
    "alembic>=1.13",
    "pydantic>=2.8",
    "pydantic-settings>=2.4",
    "boto3>=1.34",
    "rq>=1.16",
    "redis>=5.0",
    "score-schema",
]

[tool.uv.sources]
score-schema = { workspace = true }

[project.optional-dependencies]
test = ["pytest>=8.2", "httpx>=0.27"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aura_api"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 6: Write `workers/transcription/pyproject.toml`**

```toml
[project]
name = "aura-worker"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "sqlalchemy>=2.0",
    "psycopg2-binary>=2.9",
    "boto3>=1.34",
    "rq>=1.16",
    "redis>=5.0",
    "basic-pitch>=0.4",
    "mido>=1.3",
    "numpy>=1.26",
    "score-schema",
    "musicxml",
]

[tool.uv.sources]
score-schema = { workspace = true }
musicxml = { workspace = true }

[project.optional-dependencies]
test = ["pytest>=8.2", "test-fixtures"]

[tool.uv.sources.test-fixtures]
workspace = true

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/aura_worker"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 7: Write `.gitignore`**

```gitignore
__pycache__/
*.pyc
.venv/
.pytest_cache/
.ruff_cache/
*.egg-info/
dist/
.env
packages/test_fixtures/fixtures/*.wav
```

- [ ] **Step 8: Write the `Makefile`**

```makefile
.PHONY: install test lint up down

install:
	uv sync --all-packages

test:
	uv run --package score-schema pytest packages/score_schema/tests
	uv run --package musicxml pytest packages/musicxml/tests
	uv run --package aura-api pytest apps/api/tests
	uv run --package aura-worker pytest workers/transcription/tests

lint:
	uv run ruff check .

up:
	docker compose -f infra/docker-compose.yml up -d

down:
	docker compose -f infra/docker-compose.yml down
```

- [ ] **Step 9: Verify the workspace resolves**

Run: `uv sync --all-packages`
Expected: dependency resolution succeeds with no errors (test-suite packages have no code yet, so nothing to import-check).

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml Makefile .gitignore apps/api/pyproject.toml workers/transcription/pyproject.toml packages/score_schema/pyproject.toml packages/musicxml/pyproject.toml packages/test_fixtures/pyproject.toml
git commit -m "chore: scaffold monorepo workspace and package manifests"
```

---

## Task 2: Local infrastructure (Postgres, Redis, MinIO)

**Files:**
- Create: `infra/docker-compose.yml`
- Create: `infra/.env.example`

**Interfaces:**
- Produces: `DATABASE_URL`, `REDIS_URL`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET` environment variable contract that every later task's `Settings` class reads.

- [ ] **Step 1: Write `infra/docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: aura
      POSTGRES_PASSWORD: aura
      POSTGRES_DB: aura
    ports: ["5432:5432"]
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U aura"]
      interval: 2s
      timeout: 2s
      retries: 20

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 2s
      retries: 20

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: aura
      MINIO_ROOT_PASSWORD: aurasecret
    ports: ["9000:9000", "9001:9001"]
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 2s
      timeout: 2s
      retries: 20

  minio-init:
    image: minio/mc:latest
    depends_on:
      minio:
        condition: service_healthy
    entrypoint: >
      /bin/sh -c "
      mc alias set local http://minio:9000 aura aurasecret &&
      mc mb -p local/aura-media
      "
```

- [ ] **Step 2: Write `infra/.env.example`**

```dotenv
DATABASE_URL=postgresql+psycopg2://aura:aura@localhost:5432/aura
REDIS_URL=redis://localhost:6379/0
S3_ENDPOINT_URL=http://localhost:9000
S3_ACCESS_KEY=aura
S3_SECRET_KEY=aurasecret
S3_BUCKET=aura-media
S3_REGION=us-east-1
```

- [ ] **Step 3: Verify infra boots**

Run: `docker compose -f infra/docker-compose.yml up -d && docker compose -f infra/docker-compose.yml ps`
Expected: `postgres`, `redis`, `minio` show `healthy`; `minio-init` exits 0 having created bucket `aura-media`.

- [ ] **Step 4: Commit**

```bash
git add infra/docker-compose.yml infra/.env.example
git commit -m "chore: add local docker-compose infra for postgres, redis, minio"
```

---

## Task 3: `score_schema` package — canonical score contract

**Files:**
- Create: `packages/score_schema/src/score_schema/__init__.py`
- Create: `packages/score_schema/src/score_schema/models.py`
- Create: `packages/score_schema/src/score_schema/validate.py`
- Test: `packages/score_schema/tests/test_models.py`
- Test: `packages/score_schema/tests/test_validate.py`

**Interfaces:**
- Produces: `NoteEvent` dataclass, `JobErrorCode` str-enum, `build_score(instrument, time_map, measures) -> dict`, `validate_score(score: dict) -> None` (raises `ScoreValidationError`). Every later task (worker stages, musicxml package, API job status) imports `JobErrorCode` from here so error codes never diverge between services.

- [ ] **Step 1: Write the failing test for `NoteEvent` and `JobErrorCode`**

```python
# packages/score_schema/tests/test_models.py
from score_schema.models import JobErrorCode, NoteEvent, build_score


def test_note_event_is_immutable_and_typed():
    ev = NoteEvent(pitch=64, onset_s=0.61, offset_s=1.08, velocity=90, confidence=0.91)
    assert ev.pitch == 64
    assert ev.confidence == 0.91


def test_job_error_code_values_match_spec():
    assert JobErrorCode.UNSUPPORTED_MEDIA == "UNSUPPORTED_MEDIA"
    assert JobErrorCode.NO_MUSIC_DETECTED == "NO_MUSIC_DETECTED"


def test_build_score_produces_schema_v1_shape():
    score = build_score(
        instrument="guitar",
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
    assert score["schemaVersion"] == 1
    assert score["parts"][0]["instrument"] == "guitar"
    assert score["parts"][0]["measures"][0]["events"][0]["pitch"] == 64
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package score-schema pytest packages/score_schema/tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'score_schema.models'`

- [ ] **Step 3: Write the implementation**

```python
# packages/score_schema/src/score_schema/models.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class JobErrorCode(str, Enum):
    UNSUPPORTED_MEDIA = "UNSUPPORTED_MEDIA"
    MEDIA_TOO_LARGE = "MEDIA_TOO_LARGE"
    DECODE_FAILED = "DECODE_FAILED"
    NO_MUSIC_DETECTED = "NO_MUSIC_DETECTED"
    MODEL_FAILED = "MODEL_FAILED"
    EXPORT_FAILED = "EXPORT_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


@dataclass(frozen=True)
class NoteEvent:
    """A raw, performed-time note prediction from the inference stage."""

    pitch: int  # MIDI note number
    onset_s: float
    offset_s: float
    velocity: int  # 0-127
    confidence: float  # 0.0-1.0


def build_score(
    instrument: str,
    time_map: list[dict],
    measures: list[dict],
) -> dict:
    """Assemble the canonical schemaVersion-1 score JSON (ARCHITECTURE.md §5)."""
    return {
        "schemaVersion": 1,
        "timeMap": time_map,
        "parts": [
            {
                "instrument": instrument,
                "measures": measures,
            }
        ],
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package score-schema pytest packages/score_schema/tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing test for schema validation**

```python
# packages/score_schema/tests/test_validate.py
import pytest

from score_schema.models import build_score
from score_schema.validate import ScoreValidationError, validate_score


def _valid_score():
    return build_score(
        instrument="piano",
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


def test_wrong_schema_version_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 2
    with pytest.raises(ScoreValidationError):
        validate_score(score)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package score-schema pytest packages/score_schema/tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'score_schema.validate'`

- [ ] **Step 7: Write the implementation**

```python
# packages/score_schema/src/score_schema/validate.py
from __future__ import annotations

import jsonschema

_EVENT_SCHEMA = {
    "type": "object",
    "required": [
        "id", "pitch", "onsetSeconds", "offsetSeconds",
        "notatedOnset", "notatedDuration", "voice", "confidence", "locked",
    ],
    "properties": {
        "id": {"type": "string"},
        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
        "onsetSeconds": {"type": "number", "minimum": 0},
        "offsetSeconds": {"type": "number", "minimum": 0},
        "notatedOnset": {"type": "string"},
        "notatedDuration": {"type": "string"},
        "voice": {"type": "integer", "minimum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "locked": {"type": "boolean"},
    },
    "additionalProperties": False,
}

_SCORE_SCHEMA = {
    "type": "object",
    "required": ["schemaVersion", "timeMap", "parts"],
    "properties": {
        "schemaVersion": {"const": 1},
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
                "required": ["instrument", "measures"],
                "properties": {
                    "instrument": {"enum": ["guitar", "piano"]},
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


class ScoreValidationError(ValueError):
    pass


def validate_score(score: dict) -> None:
    try:
        jsonschema.validate(instance=score, schema=_SCORE_SCHEMA)
    except jsonschema.ValidationError as exc:
        raise ScoreValidationError(str(exc)) from exc
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package score-schema pytest packages/score_schema/tests -v`
Expected: PASS (7 tests total)

- [ ] **Step 9: Commit**

```bash
git add packages/score_schema
git commit -m "feat(score-schema): add canonical score model, error codes, and validation"
```

---

## Task 4: Database models and initial migration

**Files:**
- Create: `apps/api/src/aura_api/db.py`
- Create: `apps/api/src/aura_api/models.py`
- Create: `apps/api/alembic.ini`
- Create: `apps/api/alembic/env.py`
- Create: `apps/api/alembic/versions/0001_initial.py`
- Test: `apps/api/tests/conftest.py`
- Test: `apps/api/tests/test_models.py`

**Interfaces:**
- Consumes: `DATABASE_URL` from environment (Task 2).
- Produces: ORM classes `Project`, `MediaAsset`, `TranscriptionJob`, `StageArtifact`, `ScoreRevision`, `Export` and `SessionLocal` session factory, imported by every later API router and by the worker.

- [ ] **Step 1: Write `apps/api/src/aura_api/db.py`**

```python
# apps/api/src/aura_api/db.py
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def get_engine():
    url = os.environ["DATABASE_URL"]
    return create_engine(url, pool_pre_ping=True)


engine = get_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_session() -> Session:
    return SessionLocal()
```

- [ ] **Step 2: Write `apps/api/src/aura_api/models.py`**

```python
# apps/api/src/aura_api/models.py
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import JSON, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from aura_api.db import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner_id: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    instrument: Mapped[str] = mapped_column(String(16))  # "guitar" | "piano"
    tuning: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(nullable=True)

    media_assets: Mapped[list["MediaAsset"]] = relationship(back_populates="project")
    jobs: Mapped[list["TranscriptionJob"]] = relationship(back_populates="project")


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    kind: Mapped[str] = mapped_column(String(16))  # "source"
    object_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    bytes: Mapped[int | None] = mapped_column(nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(nullable=True)
    retention_until: Mapped[datetime | None] = mapped_column(nullable=True)

    project: Mapped[Project] = relationship(back_populates="media_assets")


class TranscriptionJob(Base):
    __tablename__ = "transcription_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "input_hash", name="uq_job_project_input_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    media_asset_id: Mapped[str] = mapped_column(ForeignKey("media_assets.id"))
    status: Mapped[str] = mapped_column(String(16), default="created")
    stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress: Mapped[int] = mapped_column(default=0)
    input_hash: Mapped[str] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(16), default="v1")
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    project: Mapped[Project] = relationship(back_populates="jobs")
    artifacts: Mapped[list["StageArtifact"]] = relationship(back_populates="job")
    exports: Mapped[list["Export"]] = relationship(back_populates="job")


class StageArtifact(Base):
    __tablename__ = "stage_artifacts"
    __table_args__ = (
        UniqueConstraint("job_id", "stage", "version", name="uq_artifact_job_stage_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id"))
    stage: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(default=1)
    object_key: Mapped[str] = mapped_column(String(512))
    sha256: Mapped[str] = mapped_column(String(64))
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

    job: Mapped[TranscriptionJob] = relationship(back_populates="artifacts")


class ScoreRevision(Base):
    __tablename__ = "score_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("score_revisions.id"), nullable=True)
    revision: Mapped[int] = mapped_column(default=0)
    score_json: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Export(Base):
    __tablename__ = "exports"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    job_id: Mapped[str] = mapped_column(ForeignKey("transcription_jobs.id"))
    revision: Mapped[int] = mapped_column(default=0)
    format: Mapped[str] = mapped_column(String(16))  # "midi" | "musicxml"
    status: Mapped[str] = mapped_column(String(16), default="pending")
    object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(nullable=True)

    job: Mapped[TranscriptionJob] = relationship(back_populates="exports")
```

- [ ] **Step 3: Write `apps/api/alembic.ini`**

```ini
[alembic]
script_location = alembic
sqlalchemy.url =

[loggers]
keys = root,alembic

[logger_root]
level = WARNING
handlers = console

[logger_alembic]
level = INFO
handlers =
qualname = alembic

[handlers]
keys = console

[handler_console]
class = StreamHandler
args = (sys.stderr,)
level = NOTSET
formatter = generic

[formatters]
keys = generic

[formatter_generic]
format = %(levelname)-5.5s [%(name)s] %(message)s
```

- [ ] **Step 4: Write `apps/api/alembic/env.py`**

```python
# apps/api/alembic/env.py
import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from aura_api.db import Base  # noqa: E402
from aura_api import models  # noqa: E402,F401  (import so models register on Base.metadata)

config = context.config
config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
target_metadata = Base.metadata


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
```

- [ ] **Step 5: Generate the initial migration**

Run:
```bash
cd apps/api
DATABASE_URL=postgresql+psycopg2://aura:aura@localhost:5432/aura \
  uv run alembic revision --autogenerate -m "initial schema"
```
Expected: creates `alembic/versions/0001_initial.py` (or a hash-named equivalent — rename it to `0001_initial.py`) containing `create_table` calls for all six models above.

- [ ] **Step 6: Apply the migration**

Run: `DATABASE_URL=postgresql+psycopg2://aura:aura@localhost:5432/aura uv run --package aura-api alembic -c apps/api/alembic.ini upgrade head`
Expected: exits 0; `psql $DATABASE_URL -c '\dt'` lists all six tables.

- [ ] **Step 7: Write `apps/api/tests/conftest.py`**

```python
# apps/api/tests/conftest.py
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg2://aura:aura@localhost:5432/aura"
)

from aura_api.db import Base  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()
```

- [ ] **Step 8: Write the failing test**

```python
# apps/api/tests/test_models.py
from aura_api.models import MediaAsset, Project, TranscriptionJob


def test_creating_a_project_and_job(db_session):
    project = Project(owner_id="user_1", title="My Riff", instrument="guitar")
    db_session.add(project)
    db_session.flush()

    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/riff.wav")
    db_session.add(asset)
    db_session.flush()

    job = TranscriptionJob(
        project_id=project.id, media_asset_id=asset.id, input_hash="abc123"
    )
    db_session.add(job)
    db_session.commit()

    assert job.status == "created"
    assert job.project_id == project.id


def test_duplicate_input_hash_per_project_is_rejected(db_session):
    import pytest
    from sqlalchemy.exc import IntegrityError

    project = Project(owner_id="user_1", title="My Riff", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/riff.wav")
    db_session.add(asset)
    db_session.flush()

    db_session.add(TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="dup"))
    db_session.commit()

    db_session.add(TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="dup"))
    with pytest.raises(IntegrityError):
        db_session.commit()
```

- [ ] **Step 9: Run test to verify it fails**

Run: `uv run --package aura-api pytest apps/api/tests/test_models.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_api'` (package not yet on path — resolved by Step 10's `uv sync`)

- [ ] **Step 10: Install the package in editable mode and re-run**

Run: `uv sync --package aura-api && uv run --package aura-api pytest apps/api/tests/test_models.py -v`
Expected: PASS (2 tests)

- [ ] **Step 11: Commit**

```bash
git add apps/api/src/aura_api/db.py apps/api/src/aura_api/models.py apps/api/alembic.ini apps/api/alembic apps/api/tests
git commit -m "feat(api): add ORM models and initial database migration"
```

---

## Task 5: API skeleton — config, storage client, health check

**Files:**
- Create: `apps/api/src/aura_api/config.py`
- Create: `apps/api/src/aura_api/storage.py`
- Create: `apps/api/src/aura_api/main.py`
- Test: `apps/api/tests/test_health.py`

**Interfaces:**
- Produces: `Settings` (pydantic-settings), `StorageClient.presign_put(key, content_type) -> str`, `StorageClient.presign_get(key) -> str`, `create_app() -> FastAPI`.
- Consumes: `MediaAsset`/`Project` models from Task 4.

- [ ] **Step 1: Write `apps/api/src/aura_api/config.py`**

```python
# apps/api/src/aura_api/config.py
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str
    redis_url: str
    s3_endpoint_url: str
    s3_access_key: str
    s3_secret_key: str
    s3_bucket: str
    s3_region: str = "us-east-1"
    max_upload_bytes: int = 500 * 1024 * 1024
    max_duration_ms: int = 15 * 60 * 1000


settings = Settings()
```

- [ ] **Step 2: Write `apps/api/src/aura_api/storage.py`**

```python
# apps/api/src/aura_api/storage.py
from __future__ import annotations

import boto3

from aura_api.config import settings


class StorageClient:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )
        self.bucket = settings.s3_bucket

    def presign_put(self, key: str, content_type: str, expires_in: int = 900) -> str:
        return self._client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in,
        )

    def presign_get(self, key: str, expires_in: int = 900) -> str:
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_in,
        )

    def head_object(self, key: str) -> dict | None:
        try:
            return self._client.head_object(Bucket=self.bucket, Key=key)
        except self._client.exceptions.ClientError:
            return None


storage_client = StorageClient()
```

- [ ] **Step 3: Write `apps/api/src/aura_api/main.py`**

```python
# apps/api/src/aura_api/main.py
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="AuraAudio API")

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    from aura_api.routers import exports, jobs, projects, uploads

    app.include_router(uploads.router, prefix="/v1")
    app.include_router(projects.router, prefix="/v1")
    app.include_router(jobs.router, prefix="/v1")
    app.include_router(exports.router, prefix="/v1")

    return app


app = create_app()
```

- [ ] **Step 4: Write the failing test**

```python
# apps/api/tests/test_health.py
from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_healthz_returns_ok():
    client = TestClient(create_app())
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 5: Run test to verify it fails**

Run: `uv run --package aura-api pytest apps/api/tests/test_health.py -v`
Expected: FAIL — `create_app()` imports `routers.exports/jobs/projects/uploads`, which don't exist yet (`ModuleNotFoundError: No module named 'aura_api.routers'`)

- [ ] **Step 6: Create empty router stubs so the app can boot**

```python
# apps/api/src/aura_api/routers/__init__.py
```

```python
# apps/api/src/aura_api/routers/uploads.py
from fastapi import APIRouter

router = APIRouter(tags=["uploads"])
```

```python
# apps/api/src/aura_api/routers/projects.py
from fastapi import APIRouter

router = APIRouter(tags=["projects"])
```

```python
# apps/api/src/aura_api/routers/jobs.py
from fastapi import APIRouter

router = APIRouter(tags=["jobs"])
```

```python
# apps/api/src/aura_api/routers/exports.py
from fastapi import APIRouter

router = APIRouter(tags=["exports"])
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run --package aura-api pytest apps/api/tests/test_health.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/aura_api/config.py apps/api/src/aura_api/storage.py apps/api/src/aura_api/main.py apps/api/src/aura_api/routers apps/api/tests/test_health.py
git commit -m "feat(api): add settings, storage client, app factory, and router stubs"
```

---

## Task 6: `POST /v1/uploads` — signed upload URL

**Files:**
- Modify: `apps/api/src/aura_api/routers/uploads.py`
- Create: `apps/api/src/aura_api/schemas.py`
- Test: `apps/api/tests/test_uploads.py`

**Interfaces:**
- Produces: `POST /v1/uploads` accepting `{"filename": str, "content_type": str}`, returning `{"object_key": str, "upload_url": str}`.
- Consumes: `StorageClient.presign_put` (Task 5).

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_uploads.py
from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_create_upload_returns_signed_url_and_object_key():
    client = TestClient(create_app())
    with patch("aura_api.routers.uploads.storage_client") as mock_storage:
        mock_storage.presign_put.return_value = "https://minio.local/signed"
        resp = client.post(
            "/v1/uploads", json={"filename": "riff.wav", "content_type": "audio/wav"}
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["upload_url"] == "https://minio.local/signed"
    assert body["object_key"].startswith("uploads/")
    assert body["object_key"].endswith("riff.wav")


def test_create_upload_rejects_unsupported_content_type():
    client = TestClient(create_app())
    resp = client.post(
        "/v1/uploads", json={"filename": "riff.exe", "content_type": "application/octet-stream"}
    )
    assert resp.status_code == 422
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-api pytest apps/api/tests/test_uploads.py -v`
Expected: FAIL — 404 on the endpoint (router has no route yet)

- [ ] **Step 3: Write `apps/api/src/aura_api/schemas.py`**

```python
# apps/api/src/aura_api/schemas.py
from __future__ import annotations

import uuid

from pydantic import BaseModel, field_validator

_ALLOWED_CONTENT_TYPES = {
    "audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4",
    "video/mp4", "video/quicktime",
}


class CreateUploadRequest(BaseModel):
    filename: str
    content_type: str

    @field_validator("content_type")
    @classmethod
    def content_type_supported(cls, v: str) -> str:
        if v not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"unsupported content_type: {v}")
        return v


class CreateUploadResponse(BaseModel):
    object_key: str
    upload_url: str


def make_upload_object_key(filename: str) -> str:
    return f"uploads/{uuid.uuid4()}/{filename}"
```

- [ ] **Step 4: Implement the route**

```python
# apps/api/src/aura_api/routers/uploads.py
from fastapi import APIRouter

from aura_api.schemas import CreateUploadRequest, CreateUploadResponse, make_upload_object_key
from aura_api.storage import storage_client

router = APIRouter(tags=["uploads"])


@router.post("/uploads", response_model=CreateUploadResponse, status_code=201)
def create_upload(body: CreateUploadRequest) -> CreateUploadResponse:
    object_key = make_upload_object_key(body.filename)
    upload_url = storage_client.presign_put(object_key, body.content_type)
    return CreateUploadResponse(object_key=object_key, upload_url=upload_url)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --package aura-api pytest apps/api/tests/test_uploads.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add apps/api/src/aura_api/schemas.py apps/api/src/aura_api/routers/uploads.py apps/api/tests/test_uploads.py
git commit -m "feat(api): add POST /v1/uploads signed-URL endpoint"
```

---

## Task 7: `POST /v1/projects` — register an uploaded asset

**Files:**
- Modify: `apps/api/src/aura_api/routers/projects.py`
- Modify: `apps/api/src/aura_api/schemas.py`
- Modify: `apps/api/src/aura_api/main.py` (wire `get_session` dependency)
- Create: `apps/api/src/aura_api/deps.py`
- Test: `apps/api/tests/test_projects.py`

**Interfaces:**
- Consumes: `Project`, `MediaAsset` (Task 4); `storage_client.head_object` (Task 5).
- Produces: `POST /v1/projects` accepting `{"title", "instrument", "object_key"}`, returning `{"id", "title", "instrument", "media_asset_id"}`. `get_db` FastAPI dependency reused by every later router.

- [ ] **Step 1: Write `apps/api/src/aura_api/deps.py`**

```python
# apps/api/src/aura_api/deps.py
from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import Session

from aura_api.db import SessionLocal


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

- [ ] **Step 2: Write the failing test**

```python
# apps/api/tests/test_projects.py
from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_create_project_registers_media_asset(db_session):
    client = TestClient(create_app())
    with patch("aura_api.routers.projects.storage_client") as mock_storage:
        mock_storage.head_object.return_value = {"ContentLength": 1024}
        resp = client.post(
            "/v1/projects",
            json={
                "title": "My Riff",
                "instrument": "guitar",
                "object_key": "uploads/abc/riff.wav",
            },
        )
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "My Riff"
    assert body["instrument"] == "guitar"
    assert "id" in body
    assert "media_asset_id" in body


def test_create_project_rejects_missing_object():
    client = TestClient(create_app())
    with patch("aura_api.routers.projects.storage_client") as mock_storage:
        mock_storage.head_object.return_value = None
        resp = client.post(
            "/v1/projects",
            json={"title": "X", "instrument": "piano", "object_key": "uploads/missing.wav"},
        )
    assert resp.status_code == 404
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --package aura-api pytest apps/api/tests/test_projects.py -v`
Expected: FAIL — 404 for both (no route registered)

- [ ] **Step 4: Extend `schemas.py`**

```python
# apps/api/src/aura_api/schemas.py  (append)
from pydantic import ConfigDict


class CreateProjectRequest(BaseModel):
    title: str
    instrument: str

    @field_validator("instrument")
    @classmethod
    def instrument_supported(cls, v: str) -> str:
        if v not in {"guitar", "piano"}:
            raise ValueError("instrument must be 'guitar' or 'piano'")
        return v

    object_key: str


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    instrument: str
    media_asset_id: str
```

- [ ] **Step 5: Implement the route**

```python
# apps/api/src/aura_api/routers/projects.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import MediaAsset, Project
from aura_api.schemas import CreateProjectRequest, ProjectResponse
from aura_api.storage import storage_client

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectResponse, status_code=201)
def create_project(body: CreateProjectRequest, db: Session = Depends(get_db)) -> ProjectResponse:
    head = storage_client.head_object(body.object_key)
    if head is None:
        raise HTTPException(status_code=404, detail="uploaded object not found")

    project = Project(owner_id="anonymous", title=body.title, instrument=body.instrument)
    db.add(project)
    db.flush()

    asset = MediaAsset(
        project_id=project.id,
        kind="source",
        object_key=body.object_key,
        bytes=head.get("ContentLength"),
    )
    db.add(asset)
    db.commit()

    return ProjectResponse(
        id=project.id, title=project.title, instrument=project.instrument, media_asset_id=asset.id
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --package aura-api pytest apps/api/tests/test_projects.py -v`
Expected: PASS (2 tests)

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/aura_api/deps.py apps/api/src/aura_api/schemas.py apps/api/src/aura_api/routers/projects.py apps/api/tests/test_projects.py
git commit -m "feat(api): add POST /v1/projects to register uploaded media"
```

---

## Task 8: `POST /v1/projects/{id}/transcriptions` — idempotent job creation

**Files:**
- Modify: `apps/api/src/aura_api/routers/jobs.py`
- Modify: `apps/api/src/aura_api/schemas.py`
- Create: `apps/api/src/aura_api/queue.py`
- Create: `apps/api/src/aura_api/hashing.py`
- Test: `apps/api/tests/test_idempotency.py`

**Interfaces:**
- Produces: `POST /v1/projects/{id}/transcriptions` returning `{"job_id", "status"}` — `201` on first call, `200` with the same `job_id` on a repeat call with the same effective input.
- Consumes: `enqueue_transcription_job(job_id: str)` — a thin RQ wrapper the worker (Task 10) provides the receiving end of.

- [ ] **Step 1: Write `apps/api/src/aura_api/hashing.py`**

```python
# apps/api/src/aura_api/hashing.py
from __future__ import annotations

import hashlib


def compute_input_hash(media_sha256: str | None, object_key: str, instrument: str, pipeline_version: str) -> str:
    """Derive input_hash per ARCHITECTURE.md §6.

    media_sha256 is unknown until the worker's probe stage runs, so before that
    we hash the object_key instead — stable for the same upload, and replaced
    is not needed because object_key is unique per upload already.
    """
    basis = media_sha256 or object_key
    digest_input = f"{basis}:{instrument}:{pipeline_version}".encode()
    return hashlib.sha256(digest_input).hexdigest()
```

- [ ] **Step 2: Write `apps/api/src/aura_api/queue.py`**

```python
# apps/api/src/aura_api/queue.py
from __future__ import annotations

from redis import Redis
from rq import Queue

from aura_api.config import settings

_redis = Redis.from_url(settings.redis_url)
transcription_queue = Queue("transcription", connection=_redis)


def enqueue_transcription_job(job_id: str) -> None:
    transcription_queue.enqueue("aura_worker.runner.run_transcription_job", job_id, job_id=job_id)
```

- [ ] **Step 3: Write the failing test**

```python
# apps/api/tests/test_idempotency.py
from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import MediaAsset, Project


def _seed_project(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.commit()
    return project, asset


def test_repeated_transcription_request_returns_same_job(db_session):
    project, _asset = _seed_project(db_session)
    client = TestClient(create_app())

    with patch("aura_api.routers.jobs.enqueue_transcription_job") as mock_enqueue:
        first = client.post(f"/v1/projects/{project.id}/transcriptions")
        second = client.post(f"/v1/projects/{project.id}/transcriptions")

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["job_id"] == second.json()["job_id"]
    mock_enqueue.assert_called_once()


def test_transcription_request_for_unknown_project_is_404():
    client = TestClient(create_app())
    resp = client.post("/v1/projects/does-not-exist/transcriptions")
    assert resp.status_code == 404
```

- [ ] **Step 4: Run test to verify it fails**

Run: `uv run --package aura-api pytest apps/api/tests/test_idempotency.py -v`
Expected: FAIL — 404 (route not implemented)

- [ ] **Step 5: Extend `schemas.py`**

```python
# apps/api/src/aura_api/schemas.py  (append)
class CreateJobResponse(BaseModel):
    job_id: str
    status: str
```

- [ ] **Step 6: Implement the route**

```python
# apps/api/src/aura_api/routers/jobs.py
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.hashing import compute_input_hash
from aura_api.models import MediaAsset, Project, TranscriptionJob
from aura_api.queue import enqueue_transcription_job
from aura_api.schemas import CreateJobResponse

router = APIRouter(tags=["jobs"])


@router.post("/projects/{project_id}/transcriptions", response_model=CreateJobResponse)
def create_transcription(
    project_id: str, response: Response, db: Session = Depends(get_db)
) -> CreateJobResponse:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")

    asset = (
        db.query(MediaAsset)
        .filter(MediaAsset.project_id == project_id, MediaAsset.kind == "source")
        .order_by(MediaAsset.id.desc())
        .first()
    )
    if asset is None:
        raise HTTPException(status_code=404, detail="no source media for project")

    input_hash = compute_input_hash(
        media_sha256=asset.sha256,
        object_key=asset.object_key,
        instrument=project.instrument,
        pipeline_version="v1",
    )

    existing = (
        db.query(TranscriptionJob)
        .filter(
            TranscriptionJob.project_id == project_id,
            TranscriptionJob.input_hash == input_hash,
        )
        .one_or_none()
    )
    if existing is not None:
        response.status_code = 200
        return CreateJobResponse(job_id=existing.id, status=existing.status)

    job = TranscriptionJob(
        project_id=project_id, media_asset_id=asset.id, input_hash=input_hash, status="queued"
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = (
            db.query(TranscriptionJob)
            .filter(TranscriptionJob.project_id == project_id, TranscriptionJob.input_hash == input_hash)
            .one()
        )
        response.status_code = 200
        return CreateJobResponse(job_id=existing.id, status=existing.status)

    enqueue_transcription_job(job.id)
    response.status_code = 201
    return CreateJobResponse(job_id=job.id, status=job.status)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run --package aura-api pytest apps/api/tests/test_idempotency.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/aura_api/queue.py apps/api/src/aura_api/hashing.py apps/api/src/aura_api/schemas.py apps/api/src/aura_api/routers/jobs.py apps/api/tests/test_idempotency.py
git commit -m "feat(api): add idempotent POST /v1/projects/{id}/transcriptions"
```

---

## Task 9: `GET /v1/jobs/{id}` and `GET /v1/exports/{id}`

**Files:**
- Modify: `apps/api/src/aura_api/routers/jobs.py`
- Modify: `apps/api/src/aura_api/routers/exports.py`
- Modify: `apps/api/src/aura_api/schemas.py`
- Test: `apps/api/tests/test_jobs_and_exports.py`

**Interfaces:**
- Produces: `GET /v1/jobs/{id}` → `{"id","status","stage","progress","error_code","error_detail"}`; `GET /v1/exports/{id}` → `{"id","format","status","download_url"}` (signed GET URL, only when `status == "succeeded"`).

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_jobs_and_exports.py
from unittest.mock import patch

from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import Export, MediaAsset, Project, TranscriptionJob


def test_get_job_status(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(
        project_id=project.id, media_asset_id=asset.id, input_hash="h1",
        status="running", stage="inference", progress=40,
    )
    db_session.add(job)
    db_session.commit()

    client = TestClient(create_app())
    resp = client.get(f"/v1/jobs/{job.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "running"
    assert body["stage"] == "inference"
    assert body["progress"] == 40


def test_get_job_status_404_for_unknown_job():
    client = TestClient(create_app())
    resp = client.get("/v1/jobs/does-not-exist")
    assert resp.status_code == 404


def test_get_export_returns_download_url_when_succeeded(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h2", status="succeeded")
    db_session.add(job)
    db_session.flush()
    export = Export(
        project_id=project.id, job_id=job.id, format="midi", status="succeeded",
        object_key="exports/a/out.mid",
    )
    db_session.add(export)
    db_session.commit()

    client = TestClient(create_app())
    with patch("aura_api.routers.exports.storage_client") as mock_storage:
        mock_storage.presign_get.return_value = "https://minio.local/signed-download"
        resp = client.get(f"/v1/exports/{export.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "succeeded"
    assert body["download_url"] == "https://minio.local/signed-download"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-api pytest apps/api/tests/test_jobs_and_exports.py -v`
Expected: FAIL — 404 on all routes (not implemented)

- [ ] **Step 3: Extend `schemas.py`**

```python
# apps/api/src/aura_api/schemas.py  (append)
class JobStatusResponse(BaseModel):
    id: str
    status: str
    stage: str | None
    progress: int
    error_code: str | None
    error_detail: str | None


class ExportStatusResponse(BaseModel):
    id: str
    format: str
    status: str
    download_url: str | None
```

- [ ] **Step 4: Implement `GET /v1/jobs/{id}`**

```python
# apps/api/src/aura_api/routers/jobs.py  (append)
@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    job = db.get(TranscriptionJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    return JobStatusResponse(
        id=job.id, status=job.status, stage=job.stage, progress=job.progress,
        error_code=job.error_code, error_detail=job.error_detail,
    )
```

Add `from aura_api.schemas import CreateJobResponse, JobStatusResponse` to the top of `jobs.py`.

- [ ] **Step 5: Implement `GET /v1/exports/{id}`**

```python
# apps/api/src/aura_api/routers/exports.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import Export
from aura_api.schemas import ExportStatusResponse
from aura_api.storage import storage_client

router = APIRouter(tags=["exports"])


@router.get("/exports/{export_id}", response_model=ExportStatusResponse)
def get_export(export_id: str, db: Session = Depends(get_db)) -> ExportStatusResponse:
    export = db.get(Export, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")

    download_url = None
    if export.status == "succeeded" and export.object_key:
        download_url = storage_client.presign_get(export.object_key)

    return ExportStatusResponse(
        id=export.id, format=export.format, status=export.status, download_url=download_url
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --package aura-api pytest apps/api/tests/test_jobs_and_exports.py -v`
Expected: PASS (3 tests)

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/aura_api/routers/jobs.py apps/api/src/aura_api/routers/exports.py apps/api/src/aura_api/schemas.py apps/api/tests/test_jobs_and_exports.py
git commit -m "feat(api): add GET /v1/jobs/{id} and GET /v1/exports/{id}"
```

---

## Task 10: Worker skeleton — stage runner with resume support

**Files:**
- Create: `workers/transcription/src/aura_worker/__init__.py`
- Create: `workers/transcription/src/aura_worker/stage_runner.py`
- Test: `workers/transcription/tests/conftest.py`
- Test: `workers/transcription/tests/test_stage_runner.py`

**Interfaces:**
- Produces: `StageContext(job, session, storage, workdir)`, `find_cached_artifact(ctx, stage_name, version) -> StageArtifact | None`, `save_artifact(ctx, stage_name, version, object_key, sha256, metrics) -> StageArtifact`. Each stage module (Tasks 11-14) calls `find_cached_artifact` first and returns early if a matching `StageArtifact` row already exists, otherwise does the work and calls `save_artifact` at the end — that check-then-save pair is what makes a retried job resume instead of recompute (§4.3, §6).

- [ ] **Step 1: Write `workers/transcription/tests/conftest.py`**

```python
# workers/transcription/tests/conftest.py
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg2://aura:aura@localhost:5432/aura")

from aura_api.db import Base  # noqa: E402
from aura_api.models import MediaAsset, Project, TranscriptionJob  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(os.environ["DATABASE_URL"])
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()


@pytest.fixture()
def sample_job(db_session):
    project = Project(owner_id="anonymous", title="X", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/a/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h1", status="queued")
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture()
def workdir():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)
```

> **Note:** the worker's test suite reuses `aura_api`'s `Base`/models rather than duplicating them — add `aura-api` as a workspace test dependency: append `"aura-api"` to `workers/transcription/pyproject.toml`'s `[project.optional-dependencies].test` list and `aura-api = { workspace = true }` under `[tool.uv.sources]`.

- [ ] **Step 2: Write the failing test**

```python
# workers/transcription/tests/test_stage_runner.py
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact


def test_find_cached_artifact_is_none_until_saved(db_session, sample_job, workdir):
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    assert find_cached_artifact(ctx, "probe", 1) is None

    save_artifact(ctx, "probe", 1, object_key="jobs/x/probe.json", sha256="deadbeef", metrics={"duration_ms": 2000})

    found = find_cached_artifact(ctx, "probe", 1)
    assert found is not None
    assert found.object_key == "jobs/x/probe.json"
    assert found.sha256 == "deadbeef"


def test_save_artifact_advances_job_stage_and_progress(db_session, sample_job, workdir):
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    save_artifact(ctx, "inference", 1, object_key="jobs/x/notes.json", sha256="cafebabe", metrics={})

    assert sample_job.stage == "inference"
    assert sample_job.progress == 55


def test_find_cached_artifact_is_scoped_to_stage_and_version(db_session, sample_job, workdir):
    ctx = StageContext(job=sample_job, session=db_session, storage=None, workdir=workdir)

    save_artifact(ctx, "probe", 1, object_key="jobs/x/probe.json", sha256="deadbeef", metrics={})

    assert find_cached_artifact(ctx, "probe", 2) is None
    assert find_cached_artifact(ctx, "normalize", 1) is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_stage_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_worker.stage_runner'`

- [ ] **Step 4: Write the implementation**

```python
# workers/transcription/src/aura_worker/stage_runner.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from aura_api.models import StageArtifact, TranscriptionJob

STAGE_PROGRESS = {
    "probe": 10,
    "normalize": 25,
    "inference": 55,
    "quantize": 75,
    "export": 100,
}


class StorageLike(Protocol):
    def put_bytes(self, key: str, data: bytes) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...


@dataclass
class StageContext:
    job: TranscriptionJob
    session: Session
    storage: StorageLike
    workdir: Path


def find_cached_artifact(ctx: StageContext, stage_name: str, version: int) -> StageArtifact | None:
    return (
        ctx.session.query(StageArtifact)
        .filter_by(job_id=ctx.job.id, stage=stage_name, version=version)
        .one_or_none()
    )


def save_artifact(
    ctx: StageContext,
    stage_name: str,
    version: int,
    object_key: str,
    sha256: str,
    metrics: dict,
) -> StageArtifact:
    artifact = StageArtifact(
        job_id=ctx.job.id,
        stage=stage_name,
        version=version,
        object_key=object_key,
        sha256=sha256,
        metrics=metrics,
    )
    ctx.session.add(artifact)
    ctx.job.stage = stage_name
    ctx.job.progress = STAGE_PROGRESS.get(stage_name, ctx.job.progress)
    ctx.session.commit()
    return artifact
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_stage_runner.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add workers/transcription/src/aura_worker/__init__.py workers/transcription/src/aura_worker/stage_runner.py workers/transcription/tests/conftest.py workers/transcription/tests/test_stage_runner.py workers/transcription/pyproject.toml
git commit -m "feat(worker): add resume-aware stage runner"
```

---

## Task 11: Worker storage client + FFmpeg probe stage

**Files:**
- Create: `workers/transcription/src/aura_worker/storage.py`
- Create: `workers/transcription/src/aura_worker/ffmpeg_utils.py`
- Create: `workers/transcription/src/aura_worker/stages/__init__.py`
- Create: `workers/transcription/src/aura_worker/stages/probe.py`
- Test: `workers/transcription/tests/test_ffmpeg_utils.py`
- Test: `workers/transcription/tests/test_probe.py`

**Interfaces:**
- Produces: `probe_media(path: Path) -> ProbeInfo` (duration_ms, sample_rate, codec, container), `sha256_file(path) -> str`, `stages.probe.run(ctx: StageContext) -> ProbeInfo`, cached via `save_artifact`/`find_cached_artifact` so a second call on the same job resumes without re-downloading or re-probing. Raises `JobFailure(JobErrorCode.UNSUPPORTED_MEDIA | MEDIA_TOO_LARGE | DECODE_FAILED, detail)` — a new exception type this task defines and every later stage reuses.
- Consumes: `JobErrorCode` (Task 3), `StageContext`/`find_cached_artifact`/`save_artifact` (Task 10).

- [ ] **Step 1: Write `workers/transcription/src/aura_worker/errors.py`**

```python
# workers/transcription/src/aura_worker/errors.py
from __future__ import annotations

from score_schema.models import JobErrorCode


class JobFailure(Exception):
    def __init__(self, code: JobErrorCode, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(f"{code.value}: {detail}")
```

- [ ] **Step 2: Write the failing test for `ffmpeg_utils`**

```python
# workers/transcription/tests/test_ffmpeg_utils.py
import pytest

from aura_worker.errors import JobFailure
from aura_worker.ffmpeg_utils import probe_media, sha256_file
from test_fixtures.generate import write_guitar_pluck_wav


def test_probe_media_reads_duration_and_sample_rate(workdir):
    wav_path = workdir / "fixture.wav"
    write_guitar_pluck_wav(wav_path, duration_s=2.0, sample_rate=44100)

    info = probe_media(wav_path)

    assert info.container == "wav"
    assert info.sample_rate == 44100
    assert 1900 <= info.duration_ms <= 2100


def test_probe_media_rejects_nonexistent_file(workdir):
    with pytest.raises(JobFailure) as exc_info:
        probe_media(workdir / "missing.wav")
    assert exc_info.value.code.value == "DECODE_FAILED"


def test_sha256_file_is_deterministic(workdir):
    wav_path = workdir / "fixture.wav"
    write_guitar_pluck_wav(wav_path, duration_s=1.0, sample_rate=22050)
    assert sha256_file(wav_path) == sha256_file(wav_path)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_ffmpeg_utils.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_worker.ffmpeg_utils'` (and `test_fixtures` — implemented next in Task 17, but stub it now so probe tests can run: create the minimal fixture generator first, see Step 4)

- [ ] **Step 4: Write the minimal fixture generator needed to unblock this test**

```python
# packages/test_fixtures/src/test_fixtures/__init__.py
```

```python
# packages/test_fixtures/src/test_fixtures/generate.py
from __future__ import annotations

from pathlib import Path

import numpy as np
from scipy.io import wavfile


def write_guitar_pluck_wav(path: Path, duration_s: float = 2.0, sample_rate: int = 44100) -> Path:
    """Synthesize a short, rights-free guitar-pluck-like signal: a decaying
    sum of harmonics of E2/A2/D3/G3 (open low strings), one note per 0.5s.
    This is not a real recording, but it is enough signal for basic-pitch to
    detect onsets and pitches deterministically in tests."""
    open_string_freqs = [82.41, 110.00, 146.83, 196.00]  # E2 A2 D3 G3
    t = np.linspace(0, duration_s, int(sample_rate * duration_s), endpoint=False)
    signal = np.zeros_like(t)
    note_len = 0.5
    for i, freq in enumerate(open_string_freqs):
        start = i * note_len
        end = start + note_len
        mask = (t >= start) & (t < end)
        local_t = t[mask] - start
        envelope = np.exp(-3.0 * local_t)
        harmonic = sum(np.sin(2 * np.pi * freq * (h + 1) * local_t) / (h + 1) for h in range(4))
        signal[mask] = envelope * harmonic
    signal = (signal / np.max(np.abs(signal)) * 0.8 * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), sample_rate, signal)
    return path
```

- [ ] **Step 5: Write `workers/transcription/src/aura_worker/ffmpeg_utils.py`**

```python
# workers/transcription/src/aura_worker/ffmpeg_utils.py
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from aura_worker.errors import JobFailure
from score_schema.models import JobErrorCode

_ALLOWED_CODECS = {"pcm_s16le", "mp3", "aac", "h264"}


@dataclass
class ProbeInfo:
    container: str
    codec: str
    duration_ms: int
    sample_rate: int


def probe_media(path: Path) -> ProbeInfo:
    if not path.exists():
        raise JobFailure(JobErrorCode.DECODE_FAILED, f"file not found: {path}")

    try:
        proc = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=30, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise JobFailure(JobErrorCode.DECODE_FAILED, f"ffprobe failed: {exc}") from exc

    data = json.loads(proc.stdout)
    audio_streams = [s for s in data.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio_streams:
        raise JobFailure(JobErrorCode.UNSUPPORTED_MEDIA, "no audio stream found")

    stream = audio_streams[0]
    codec = stream.get("codec_name", "")
    if codec not in _ALLOWED_CODECS:
        raise JobFailure(JobErrorCode.UNSUPPORTED_MEDIA, f"unsupported codec: {codec}")

    fmt = data.get("format", {})
    duration_s = float(fmt.get("duration", stream.get("duration", 0)))
    container = Path(path).suffix.lstrip(".").lower() or fmt.get("format_name", "").split(",")[0]

    return ProbeInfo(
        container=container,
        codec=codec,
        duration_ms=int(duration_s * 1000),
        sample_rate=int(stream.get("sample_rate", 0)),
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_ffmpeg_utils.py -v`
Expected: PASS (3 tests). Requires `ffmpeg`/`ffprobe` on `PATH` — install via the worker Dockerfile (Task 19) and locally via the OS package manager for development.

- [ ] **Step 7: Write the failing test for the probe stage**

```python
# workers/transcription/tests/test_probe.py
from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import probe
from test_fixtures.generate import write_guitar_pluck_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]

    def download_media_asset(self, object_key: str, dest: "Path"):
        # In tests we pre-place the fixture at dest ourselves; production
        # implementation (Task 12) downloads from MinIO.
        raise NotImplementedError


def test_probe_stage_updates_media_asset_and_returns_info(db_session, sample_job, workdir, monkeypatch):
    from pathlib import Path

    fixture_path = workdir / "source.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0)

    storage = FakeStorage()
    monkeypatch.setattr(storage, "download_media_asset", lambda object_key, dest: fixture_path)

    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)
    info = probe.run(ctx)

    assert info.sample_rate == 44100
    asset = sample_job.media_asset if hasattr(sample_job, "media_asset") else None
    from aura_api.models import MediaAsset
    refreshed = db_session.get(MediaAsset, sample_job.media_asset_id)
    assert refreshed.sha256 is not None
    assert refreshed.duration_ms is not None


def test_probe_stage_rejects_media_exceeding_duration_limit(db_session, sample_job, workdir, monkeypatch):
    fixture_path = workdir / "source.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0)

    storage = FakeStorage()
    monkeypatch.setattr(storage, "download_media_asset", lambda object_key, dest: fixture_path)
    monkeypatch.setattr("aura_worker.stages.probe.MAX_DURATION_MS", 1000)

    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)
    try:
        probe.run(ctx)
        assert False, "expected JobFailure"
    except JobFailure as exc:
        assert exc.code.value == "MEDIA_TOO_LARGE"


def test_probe_stage_second_call_resumes_without_redownloading(db_session, sample_job, workdir, monkeypatch):
    fixture_path = workdir / "source.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0)

    storage = FakeStorage()
    download_calls = {"count": 0}

    def fake_download(object_key, dest):
        download_calls["count"] += 1
        return fixture_path

    monkeypatch.setattr(storage, "download_media_asset", fake_download)

    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)
    first_info = probe.run(ctx)
    second_info = probe.run(ctx)

    assert download_calls["count"] == 1  # second call resumed from the cached artifact
    assert first_info == second_info
```

- [ ] **Step 8: Run test to verify it fails**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_probe.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_worker.stages.probe'`

- [ ] **Step 9: Write `workers/transcription/src/aura_worker/stages/probe.py`**

```python
# workers/transcription/src/aura_worker/stages/probe.py
from __future__ import annotations

from aura_api.models import MediaAsset
from aura_worker.errors import JobFailure
from aura_worker.ffmpeg_utils import ProbeInfo, probe_media, sha256_file
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import JobErrorCode

MAX_DURATION_MS = 15 * 60 * 1000
MAX_BYTES = 500 * 1024 * 1024
STAGE_VERSION = 1


def run(ctx: StageContext) -> ProbeInfo:
    cached = find_cached_artifact(ctx, "probe", STAGE_VERSION)
    if cached is not None:
        return ProbeInfo(
            container=cached.metrics["container"],
            codec=cached.metrics["codec"],
            duration_ms=cached.metrics["duration_ms"],
            sample_rate=cached.metrics["sample_rate"],
        )

    asset = ctx.session.get(MediaAsset, ctx.job.media_asset_id)
    local_path = ctx.workdir / "source" / "input"
    local_path.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage.download_media_asset(asset.object_key, local_path)

    info = probe_media(local_path)
    if info.duration_ms > MAX_DURATION_MS:
        raise JobFailure(JobErrorCode.MEDIA_TOO_LARGE, f"duration {info.duration_ms}ms exceeds limit")

    digest = sha256_file(local_path)
    asset.sha256 = digest
    asset.duration_ms = info.duration_ms
    ctx.session.commit()

    save_artifact(
        ctx, "probe", STAGE_VERSION,
        object_key=f"jobs/{ctx.job.id}/stage/probe.json", sha256=digest,
        metrics={
            "container": info.container, "codec": info.codec,
            "duration_ms": info.duration_ms, "sample_rate": info.sample_rate,
        },
    )
    return info
```

- [ ] **Step 10: Run test to verify it passes**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_probe.py -v`
Expected: PASS (3 tests)

- [ ] **Step 11: Commit**

```bash
git add packages/test_fixtures/src workers/transcription/src/aura_worker/errors.py workers/transcription/src/aura_worker/ffmpeg_utils.py workers/transcription/src/aura_worker/stages workers/transcription/tests/test_ffmpeg_utils.py workers/transcription/tests/test_probe.py
git commit -m "feat(worker): add ffmpeg probing, sha256 hashing, and the probe stage"
```

---

## Task 12: Worker storage client (real MinIO I/O) + normalize stage

**Files:**
- Modify: `workers/transcription/src/aura_worker/storage.py`
- Create: `workers/transcription/src/aura_worker/stages/normalize.py`
- Test: `workers/transcription/tests/test_normalize.py`

**Interfaces:**
- Produces: `WorkerStorageClient.download_media_asset(object_key, dest_path)`, `WorkerStorageClient.put_bytes(key, data)`, `WorkerStorageClient.get_bytes(key)`; `stages.normalize.run(ctx, probe_info) -> Path` (path to a normalized mono 22.05kHz WAV, matching `basic-pitch`'s expected input rate).

- [ ] **Step 1: Write `workers/transcription/src/aura_worker/storage.py`**

```python
# workers/transcription/src/aura_worker/storage.py
from __future__ import annotations

import os
from pathlib import Path

import boto3


class WorkerStorageClient:
    def __init__(self) -> None:
        self._client = boto3.client(
            "s3",
            endpoint_url=os.environ["S3_ENDPOINT_URL"],
            aws_access_key_id=os.environ["S3_ACCESS_KEY"],
            aws_secret_access_key=os.environ["S3_SECRET_KEY"],
            region_name=os.environ.get("S3_REGION", "us-east-1"),
        )
        self.bucket = os.environ["S3_BUCKET"]

    def download_media_asset(self, object_key: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, object_key, str(dest_path))
        return dest_path

    def put_bytes(self, key: str, data: bytes) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:
        obj = self._client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def put_file(self, key: str, local_path: Path) -> None:
        self._client.upload_file(str(local_path), self.bucket, key)

    def get_file(self, key: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(dest_path))
        return dest_path
```

- [ ] **Step 2: Write the failing test for the normalize stage**

```python
# workers/transcription/tests/test_normalize.py
import wave

from aura_worker.stage_runner import StageContext
from aura_worker.stages import normalize
from test_fixtures.generate import write_guitar_pluck_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def test_normalize_stage_produces_mono_22050hz_wav(db_session, sample_job, workdir):
    source_path = workdir / "source" / "input"
    source_path.parent.mkdir(parents=True)
    write_guitar_pluck_wav(source_path.with_suffix(".wav"), duration_s=1.0, sample_rate=44100)
    source_path.write_bytes(source_path.with_suffix(".wav").read_bytes())

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    normalized_path = normalize.run(ctx, source_path=source_path.with_suffix(".wav"))

    with wave.open(str(normalized_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getframerate() == 22050

    assert any(key.endswith("normalized.wav") for key in storage.objects)
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_normalize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_worker.stages.normalize'`

- [ ] **Step 4: Write the implementation**

```python
# workers/transcription/src/aura_worker/stages/normalize.py
from __future__ import annotations

import subprocess
from pathlib import Path

from aura_worker.errors import JobFailure
from aura_worker.ffmpeg_utils import sha256_file
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import JobErrorCode

TARGET_SAMPLE_RATE = 22050
STAGE_VERSION = 1


def run(ctx: StageContext, source_path: Path) -> Path:
    out_path = ctx.workdir / "normalized.wav"
    key = f"jobs/{ctx.job.id}/stage/normalized.wav"

    cached = find_cached_artifact(ctx, "normalize", STAGE_VERSION)
    if cached is not None:
        out_path.write_bytes(ctx.storage.get_bytes(cached.object_key))
        return out_path

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", str(source_path),
                "-ac", "1", "-ar", str(TARGET_SAMPLE_RATE),
                "-af", "loudnorm=I=-23:TP=-2:LRA=7",
                str(out_path),
            ],
            capture_output=True, timeout=120, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise JobFailure(JobErrorCode.DECODE_FAILED, f"ffmpeg normalize failed: {exc.stderr!r}") from exc

    ctx.storage.put_bytes(key, out_path.read_bytes())
    save_artifact(
        ctx, "normalize", STAGE_VERSION, object_key=key,
        sha256=sha256_file(out_path), metrics={"sample_rate": TARGET_SAMPLE_RATE},
    )
    return out_path
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_normalize.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add workers/transcription/src/aura_worker/storage.py workers/transcription/src/aura_worker/stages/normalize.py workers/transcription/tests/test_normalize.py
git commit -m "feat(worker): add MinIO storage client and audio normalize stage"
```

---

## Task 13: Inference stage — `basic-pitch` note detection

**Files:**
- Create: `workers/transcription/src/aura_worker/stages/inference.py`
- Test: `workers/transcription/tests/test_inference.py`

**Interfaces:**
- Consumes: `NoteEvent`, `JobErrorCode` (Task 3); normalized WAV path (Task 12).
- Produces: `stages.inference.run(ctx, normalized_path) -> list[NoteEvent]`. Raises `JobFailure(JobErrorCode.NO_MUSIC_DETECTED)` when zero notes are predicted, `JobFailure(JobErrorCode.MODEL_FAILED)` on any model exception.

- [ ] **Step 1: Write the failing test**

```python
# workers/transcription/tests/test_inference.py
import pytest

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import inference
from test_fixtures.generate import write_guitar_pluck_wav


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def test_inference_detects_notes_in_fixture(db_session, sample_job, workdir):
    wav_path = workdir / "normalized.wav"
    write_guitar_pluck_wav(wav_path, duration_s=2.0, sample_rate=22050)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    notes = inference.run(ctx, normalized_path=wav_path)

    assert len(notes) > 0
    assert all(0 <= n.pitch <= 127 for n in notes)
    assert all(n.offset_s > n.onset_s for n in notes)


def test_inference_raises_no_music_detected_on_silence(db_session, sample_job, workdir):
    import numpy as np
    from scipy.io import wavfile

    silence = np.zeros(22050 * 2, dtype=np.int16)
    wav_path = workdir / "normalized.wav"
    wavfile.write(str(wav_path), 22050, silence)

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    with pytest.raises(JobFailure) as exc_info:
        inference.run(ctx, normalized_path=wav_path)
    assert exc_info.value.code.value == "NO_MUSIC_DETECTED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_inference.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_worker.stages.inference'`

- [ ] **Step 3: Write the implementation**

```python
# workers/transcription/src/aura_worker/stages/inference.py
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import JobErrorCode, NoteEvent

STAGE_VERSION = 1


def run(ctx: StageContext, normalized_path: Path) -> list[NoteEvent]:
    cached = find_cached_artifact(ctx, "inference", STAGE_VERSION)
    if cached is not None:
        raw = json.loads(ctx.storage.get_bytes(cached.object_key))
        return [NoteEvent(**item) for item in raw]

    try:
        from basic_pitch.inference import predict
        from basic_pitch import ICASSP_2022_MODEL_PATH

        _, _, note_events = predict(str(normalized_path), model_or_model_path=ICASSP_2022_MODEL_PATH)
    except JobFailure:
        raise
    except Exception as exc:  # basic-pitch/tensorflow errors are not a stable type to catch narrowly
        raise JobFailure(JobErrorCode.MODEL_FAILED, f"inference failed: {exc}") from exc

    # note_events entries are (start_time_s, end_time_s, pitch_midi, amplitude, pitch_bends);
    # pitch_midi is already a MIDI note number.
    notes = [
        NoteEvent(
            pitch=int(round(pitch_midi)),
            onset_s=float(start_s),
            offset_s=float(end_s),
            velocity=int(round(min(max(amplitude, 0.0), 1.0) * 127)),
            confidence=float(min(max(amplitude, 0.0), 1.0)),
        )
        for start_s, end_s, pitch_midi, amplitude, *_rest in note_events
    ]

    if not notes:
        raise JobFailure(JobErrorCode.NO_MUSIC_DETECTED, "model returned zero note events")

    key = f"jobs/{ctx.job.id}/stage/notes.json"
    payload = json.dumps([n.__dict__ for n in notes]).encode()
    ctx.storage.put_bytes(key, payload)
    save_artifact(
        ctx, "inference", STAGE_VERSION, object_key=key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={"note_count": len(notes)},
    )
    return notes
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_inference.py -v`
Expected: PASS (2 tests). First run downloads the `basic-pitch` ICASSP-2022 model weights (~20MB) — allow extra time and network access for this one test run; subsequent runs use the local model cache.

- [ ] **Step 5: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/inference.py workers/transcription/tests/test_inference.py
git commit -m "feat(worker): add basic-pitch inference stage"
```

---

## Task 14: Quantize stage — fixed-grid canonical score

**Files:**
- Create: `workers/transcription/src/aura_worker/stages/quantize.py`
- Test: `workers/transcription/tests/test_quantize.py`

**Interfaces:**
- Consumes: `list[NoteEvent]` (Task 13), `build_score`/`validate_score` (Task 3).
- Produces: `stages.quantize.run(ctx, notes) -> dict` (a validated canonical score at a fixed 120 BPM / 4/4 grid, snapped to 16th notes — full beat/meter estimation is Phase 2). Also persists a `ScoreRevision(revision=0)` row.

- [ ] **Step 1: Write the failing test**

```python
# workers/transcription/tests/test_quantize.py
from score_schema.models import NoteEvent
from score_schema.validate import validate_score

from aura_worker.stage_runner import StageContext
from aura_worker.stages import quantize


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def test_quantize_snaps_notes_to_sixteenth_grid_and_produces_valid_score(db_session, sample_job, workdir):
    notes = [
        NoteEvent(pitch=64, onset_s=0.02, offset_s=0.48, velocity=90, confidence=0.9),
        NoteEvent(pitch=67, onset_s=0.53, offset_s=0.97, velocity=85, confidence=0.85),
    ]

    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes)

    validate_score(score)  # must not raise
    events = score["parts"][0]["measures"][0]["events"]
    assert events[0]["pitch"] == 64
    assert events[0]["notatedOnset"] == "0/1"
    assert events[0]["notatedDuration"] == "1/4"  # ~0.46s snapped to a quarter note at 120 BPM

    from aura_api.models import ScoreRevision
    revision = db_session.query(ScoreRevision).filter_by(project_id=sample_job.project_id).one()
    assert revision.revision == 0
    assert revision.score_json["schemaVersion"] == 1


def test_quantize_places_far_notes_in_later_measures(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=60, onset_s=9.0, offset_s=9.4, velocity=80, confidence=0.7)]
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = quantize.run(ctx, notes)

    # 9.0s at 120 BPM (0.5s/beat) = beat 18 = measure 5 (4 beats/measure, 1-indexed)
    measure_numbers = [m["number"] for m in score["parts"][0]["measures"]]
    assert 5 in measure_numbers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_quantize.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_worker.stages.quantize'`

- [ ] **Step 3: Write the implementation**

```python
# workers/transcription/src/aura_worker/stages/quantize.py
from __future__ import annotations

import hashlib
import json
from fractions import Fraction

from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.models import NoteEvent, build_score
from score_schema.validate import validate_score

STAGE_VERSION = 1
BPM = 120
SECONDS_PER_BEAT = 60.0 / BPM
BEATS_PER_MEASURE = 4
GRID_BEATS = Fraction(1, 4)  # snap to 16th notes (1/4 of a beat, since a beat = quarter note)


def _seconds_to_beats(seconds: float) -> Fraction:
    raw_beats = Fraction(seconds / SECONDS_PER_BEAT).limit_denominator(64)
    return round(raw_beats / GRID_BEATS) * GRID_BEATS


def _beats_to_notated_fraction(beats: Fraction) -> str:
    """Notated duration/onset as a fraction of a whole note (4 beats)."""
    whole_note_fraction = beats / 4
    return f"{whole_note_fraction.numerator}/{whole_note_fraction.denominator}"


def run(ctx: StageContext, notes: list[NoteEvent]) -> dict:
    cached = find_cached_artifact(ctx, "quantize", STAGE_VERSION)
    if cached is not None:
        return json.loads(ctx.storage.get_bytes(cached.object_key))

    measures: dict[int, list[dict]] = {}

    for i, note in enumerate(notes):
        onset_beats = _seconds_to_beats(note.onset_s)
        offset_beats = _seconds_to_beats(note.offset_s)
        duration_beats = max(offset_beats - onset_beats, GRID_BEATS)

        measure_number = int(onset_beats // BEATS_PER_MEASURE) + 1
        onset_within_measure = onset_beats - (measure_number - 1) * BEATS_PER_MEASURE

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
        {"beat": 1, "seconds": SECONDS_PER_BEAT},
    ]

    score = build_score(instrument=ctx.job.project.instrument, time_map=time_map, measures=measure_list)
    validate_score(score)

    key = f"jobs/{ctx.job.id}/stage/score.json"
    payload = json.dumps(score).encode()
    ctx.storage.put_bytes(key, payload)

    from aura_api.models import ScoreRevision

    revision = ScoreRevision(
        project_id=ctx.job.project_id, parent_id=None, revision=0,
        score_json=score, created_by="system",
    )
    ctx.session.add(revision)
    save_artifact(
        ctx, "quantize", STAGE_VERSION, object_key=key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={"measure_count": len(measure_list)},
    )

    return score
```

`ctx.job.project` requires the `TranscriptionJob.project` relationship to be loaded; since `ctx.job` is fetched via `ctx.session.get(TranscriptionJob, job_id)` in `runner.py` (Task 16) within the same session, lazy-loading `job.project` works without extra wiring.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_quantize.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/quantize.py workers/transcription/tests/test_quantize.py
git commit -m "feat(worker): add fixed-grid quantize stage producing canonical score JSON"
```

---

## Task 15: `musicxml` package — export and round-trip validation

**Files:**
- Create: `packages/musicxml/src/musicxml/__init__.py`
- Create: `packages/musicxml/src/musicxml/export.py`
- Create: `packages/musicxml/src/musicxml/validate.py`
- Test: `packages/musicxml/tests/test_export.py`
- Test: `packages/musicxml/tests/test_validate.py`

**Interfaces:**
- Consumes: canonical score dict (Task 3/14).
- Produces: `score_json_to_musicxml(score: dict, out_path: Path) -> Path`, `reopen_and_check(path: Path, expected_note_count: int) -> None` (raises `MusicXmlValidationError` on mismatch or parse failure).

- [ ] **Step 1: Write the failing test for export**

```python
# packages/musicxml/tests/test_export.py
from pathlib import Path

from score_schema.models import build_score

from musicxml.export import score_json_to_musicxml


def _sample_score():
    return build_score(
        instrument="guitar",
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package musicxml pytest packages/musicxml/tests/test_export.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'musicxml.export'`

- [ ] **Step 3: Write the implementation**

```python
# packages/musicxml/src/musicxml/export.py
from __future__ import annotations

from fractions import Fraction
from pathlib import Path

from music21 import duration, instrument, meter, note, stream, tempo


def _notated_fraction_to_quarter_length(value: str) -> float:
    """A notated value like '1/4' means one quarter of a whole note (a
    quarter note = 1.0 quarterLength in music21), so quarterLength = 4 * fraction."""
    return float(Fraction(value) * 4)


def score_json_to_musicxml(score: dict, out_path: Path) -> Path:
    part_data = score["parts"][0]
    m21_part = stream.Part()
    m21_part.insert(0, meter.TimeSignature("4/4"))
    m21_part.insert(0, tempo.MetronomeMark(number=120))
    m21_part.insert(0, instrument.Guitar() if part_data["instrument"] == "guitar" else instrument.Piano())

    for measure_data in part_data["measures"]:
        m21_measure = stream.Measure(number=measure_data["number"])
        for event in measure_data["events"]:
            n = note.Note(event["pitch"])
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

Run: `uv run --package musicxml pytest packages/musicxml/tests/test_export.py -v`
Expected: PASS

- [ ] **Step 5: Write the failing test for validation**

```python
# packages/musicxml/tests/test_validate.py
from pathlib import Path

import pytest

from musicxml.export import score_json_to_musicxml
from musicxml.validate import MusicXmlValidationError, reopen_and_check

from .test_export import _sample_score


def test_reopen_and_check_accepts_a_well_formed_export(tmp_path: Path):
    out_path = tmp_path / "out.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    reopen_and_check(out_path, expected_note_count=2)  # must not raise


def test_reopen_and_check_rejects_note_count_mismatch(tmp_path: Path):
    out_path = tmp_path / "out.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    with pytest.raises(MusicXmlValidationError):
        reopen_and_check(out_path, expected_note_count=99)


def test_reopen_and_check_rejects_malformed_file(tmp_path: Path):
    bad_path = tmp_path / "bad.musicxml"
    bad_path.write_text("not xml at all")
    with pytest.raises(MusicXmlValidationError):
        reopen_and_check(bad_path, expected_note_count=0)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package musicxml pytest packages/musicxml/tests/test_validate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'musicxml.validate'`

- [ ] **Step 7: Write the implementation**

```python
# packages/musicxml/src/musicxml/validate.py
from __future__ import annotations

from pathlib import Path

from music21 import converter


class MusicXmlValidationError(ValueError):
    pass


def reopen_and_check(path: Path, expected_note_count: int) -> None:
    try:
        parsed = converter.parse(str(path))
    except Exception as exc:
        raise MusicXmlValidationError(f"failed to reopen {path}: {exc}") from exc

    notes = list(parsed.flatten().notes)
    if len(notes) != expected_note_count:
        raise MusicXmlValidationError(
            f"expected {expected_note_count} notes, reopened file has {len(notes)}"
        )
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package musicxml pytest packages/musicxml/tests -v`
Expected: PASS (5 tests total)

- [ ] **Step 9: Commit**

```bash
git add packages/musicxml
git commit -m "feat(musicxml): add score-to-MusicXML export and reopen validation"
```

---

## Task 16: Export stage + job runner (ties every stage together)

**Files:**
- Create: `workers/transcription/src/aura_worker/stages/export.py`
- Create: `workers/transcription/src/aura_worker/runner.py`
- Test: `workers/transcription/tests/test_export.py`

**Interfaces:**
- Consumes: `list[NoteEvent]` and score dict (Tasks 13-14), `score_json_to_musicxml`/`reopen_and_check` (Task 15), `mido`.
- Produces: `stages.export.run(ctx, notes, score) -> dict` (writes MIDI from raw performed notes + MusicXML from the quantized score, creates `Export` rows, marks the job `succeeded`). `runner.run_transcription_job(job_id: str)` — the RQ entrypoint referenced by `apps/api/src/aura_api/queue.py` (Task 8).

- [ ] **Step 1: Write the failing test for the export stage**

```python
# workers/transcription/tests/test_export.py
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


def test_export_stage_writes_midi_and_musicxml_and_creates_export_rows(db_session, sample_job, workdir):
    notes = [NoteEvent(pitch=64, onset_s=0.0, offset_s=0.5, velocity=90, confidence=0.9)]
    score = build_score(
        instrument="guitar",
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_export.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'aura_worker.stages.export'`

- [ ] **Step 3: Write the implementation**

```python
# workers/transcription/src/aura_worker/stages/export.py
from __future__ import annotations

from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from musicxml.export import score_json_to_musicxml
from musicxml.validate import MusicXmlValidationError, reopen_and_check
from score_schema.models import JobErrorCode, NoteEvent


def _write_midi(notes: list[NoteEvent], out_path) -> None:
    import mido

    mid = mido.MidiFile()
    track = mido.MidiTrack()
    mid.tracks.append(track)
    ticks_per_beat = mid.ticks_per_beat  # default 480
    tempo_us = mido.bpm2tempo(120)
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


def run(ctx: StageContext, notes: list[NoteEvent], score: dict) -> dict:
    from aura_api.models import Export

    midi_path = ctx.workdir / "output.mid"
    musicxml_path = ctx.workdir / "output.musicxml"

    _write_midi(notes, midi_path)
    score_json_to_musicxml(score, musicxml_path)

    expected_note_count = sum(
        len(measure["events"]) for measure in score["parts"][0]["measures"]
    )
    try:
        reopen_and_check(musicxml_path, expected_note_count=expected_note_count)
    except MusicXmlValidationError as exc:
        raise JobFailure(JobErrorCode.EXPORT_FAILED, str(exc)) from exc

    midi_key = f"jobs/{ctx.job.id}/exports/output.mid"
    musicxml_key = f"jobs/{ctx.job.id}/exports/output.musicxml"
    ctx.storage.put_bytes(midi_key, midi_path.read_bytes())
    ctx.storage.put_bytes(musicxml_key, musicxml_path.read_bytes())

    ctx.session.add(Export(
        project_id=ctx.job.project_id, job_id=ctx.job.id, revision=0,
        format="midi", status="succeeded", object_key=midi_key,
    ))
    ctx.session.add(Export(
        project_id=ctx.job.project_id, job_id=ctx.job.id, revision=0,
        format="musicxml", status="succeeded", object_key=musicxml_key,
    ))
    ctx.job.status = "succeeded"
    ctx.job.stage = "export"
    ctx.job.progress = 100
    ctx.session.commit()

    return {"midi_key": midi_key, "musicxml_key": musicxml_key}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest workers/transcription/tests/test_export.py -v`
Expected: PASS

- [ ] **Step 5: Write `runner.py` (no separate test — covered by Task 18's end-to-end test)**

```python
# workers/transcription/src/aura_worker/runner.py
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from aura_api.db import get_engine
from aura_api.models import TranscriptionJob
from aura_worker.errors import JobFailure
from aura_worker.stage_runner import StageContext
from aura_worker.stages import export as export_stage
from aura_worker.stages import inference, normalize, probe, quantize
from aura_worker.storage import WorkerStorageClient

logger = logging.getLogger(__name__)

_SessionLocal = sessionmaker(bind=get_engine())


def run_transcription_job(job_id: str) -> None:
    session: Session = _SessionLocal()
    storage = WorkerStorageClient()
    try:
        job = session.get(TranscriptionJob, job_id)
        if job is None:
            logger.error("job %s not found", job_id)
            return
        if job.status == "succeeded":
            logger.info("job %s already succeeded, skipping re-run", job_id)
            return

        job.status = "running"
        session.commit()

        with tempfile.TemporaryDirectory() as tmp:
            ctx = StageContext(job=job, session=session, storage=storage, workdir=Path(tmp))

            probe_info = probe.run(ctx)
            normalized_path = normalize.run(ctx, source_path=ctx.workdir / "source" / "input")
            notes = inference.run(ctx, normalized_path=normalized_path)
            score = quantize.run(ctx, notes)
            export_stage.run(ctx, notes=notes, score=score)

    except JobFailure as exc:
        session.rollback()
        job = session.get(TranscriptionJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error_code = exc.code.value
            job.error_detail = exc.detail
            session.commit()
    except Exception as exc:  # unexpected — still record for the API to surface
        session.rollback()
        job = session.get(TranscriptionJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error_code = "INTERNAL_ERROR"
            job.error_detail = str(exc)
            session.commit()
        raise
    finally:
        session.close()
```

`probe.run` writes the downloaded source to `ctx.workdir / "source" / "input"` (Task 11 Step 9) and `normalize.run` expects that exact path as `source_path` — this couples the two by convention (a fixed workdir path) rather than by return value, since `probe.run`'s return type stays just `ProbeInfo` per Task 11's interface. Note this coupling in a one-line comment above the `normalize.run` call in `runner.py`.

- [ ] **Step 6: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/export.py workers/transcription/src/aura_worker/runner.py workers/transcription/tests/test_export.py
git commit -m "feat(worker): add export stage and the run_transcription_job entrypoint"
```

---

## Task 17: `test_fixtures` package completion

**Files:**
- Modify: `packages/test_fixtures/src/test_fixtures/generate.py` (already created in Task 11 Step 4 — this task just adds its own direct test coverage)
- Test: `packages/test_fixtures/tests/test_generate.py`

**Interfaces:**
- Produces: guarantees `write_guitar_pluck_wav` is independently tested (Task 11 only exercised it indirectly through `probe`/`inference`/`normalize` tests).

- [ ] **Step 1: Write the failing test**

```python
# packages/test_fixtures/tests/test_generate.py
import wave
from pathlib import Path

from test_fixtures.generate import write_guitar_pluck_wav


def test_write_guitar_pluck_wav_produces_correct_duration_and_rate(tmp_path: Path):
    out_path = tmp_path / "fixture.wav"
    write_guitar_pluck_wav(out_path, duration_s=2.0, sample_rate=44100)

    with wave.open(str(out_path), "rb") as wf:
        assert wf.getframerate() == 44100
        frames = wf.getnframes()
        assert abs(frames / 44100 - 2.0) < 0.01


def test_write_guitar_pluck_wav_is_not_silent(tmp_path: Path):
    import numpy as np
    from scipy.io import wavfile

    out_path = tmp_path / "fixture.wav"
    write_guitar_pluck_wav(out_path, duration_s=1.0, sample_rate=22050)
    _, data = wavfile.read(str(out_path))
    assert np.max(np.abs(data)) > 1000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package test-fixtures pytest packages/test_fixtures/tests/test_generate.py -v`
Expected: FAIL — package not yet installed in editable mode for direct `uv run --package test-fixtures` invocation (its `pyproject.toml` from Task 1 exists, but nothing has run `uv sync` scoped to it with a test extra yet)

- [ ] **Step 3: Add a `[project.optional-dependencies]` test extra and sync**

```toml
# packages/test_fixtures/pyproject.toml (append)
[project.optional-dependencies]
test = ["pytest>=8.2"]
```

Run: `uv sync --package test-fixtures`

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package test-fixtures pytest packages/test_fixtures/tests/test_generate.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add packages/test_fixtures
git commit -m "test(test-fixtures): add direct coverage for the synthetic fixture generator"
```

---

## Task 18: End-to-end integration test — the Phase 1 exit criterion

**Files:**
- Test: `apps/api/tests/test_e2e_pipeline.py`

**Interfaces:**
- Exercises the full HTTP surface (`apps/api`) plus a direct in-process call to `aura_worker.runner.run_transcription_job` (standing in for the RQ worker process, since spinning up a real worker in a test is out of scope — the RQ wiring itself is exercised separately by mocking `enqueue_transcription_job` in Task 8's tests). Uploads bytes directly to MinIO (bypassing the API, matching real client behavior against a signed URL) via boto3.

- [ ] **Step 1: Write the failing test**

```python
# apps/api/tests/test_e2e_pipeline.py
import io

import boto3
import pytest
from fastapi.testclient import TestClient

from aura_api.config import settings
from aura_api.main import create_app
from aura_worker.runner import run_transcription_job
from test_fixtures.generate import write_guitar_pluck_wav


@pytest.fixture()
def s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )


def test_full_pipeline_upload_to_export_is_idempotent(db_session, tmp_path, s3_client):
    client = TestClient(create_app())

    fixture_path = tmp_path / "riff.wav"
    write_guitar_pluck_wav(fixture_path, duration_s=2.0, sample_rate=44100)

    upload_resp = client.post(
        "/v1/uploads", json={"filename": "riff.wav", "content_type": "audio/wav"}
    )
    assert upload_resp.status_code == 201
    object_key = upload_resp.json()["object_key"]

    s3_client.put_object(Bucket=settings.s3_bucket, Key=object_key, Body=fixture_path.read_bytes())

    project_resp = client.post(
        "/v1/projects",
        json={"title": "E2E Riff", "instrument": "guitar", "object_key": object_key},
    )
    assert project_resp.status_code == 201
    project_id = project_resp.json()["id"]

    job_resp_1 = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp_1.status_code == 201
    job_id = job_resp_1.json()["job_id"]

    # Run the worker in-process (stands in for the RQ worker process).
    run_transcription_job(job_id)

    status_resp = client.get(f"/v1/jobs/{job_id}")
    assert status_resp.json()["status"] == "succeeded", status_resp.json()

    from aura_api.models import Export

    exports = db_session.query(Export).filter_by(job_id=job_id).all()
    assert {e.format for e in exports} == {"midi", "musicxml"}

    export_id = next(e.id for e in exports if e.format == "midi")
    export_resp = client.get(f"/v1/exports/{export_id}")
    assert export_resp.status_code == 200
    download_url = export_resp.json()["download_url"]
    assert download_url is not None

    import urllib.request

    with urllib.request.urlopen(download_url) as f:
        midi_bytes = f.read()
    assert midi_bytes[:4] == b"MThd"  # valid MIDI file header

    # Re-request transcription for the same project: must return the same job,
    # and re-running the worker on an already-succeeded job must not recompute
    # any stage (StageArtifact rows are reused) or create duplicate exports.
    job_resp_2 = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp_2.status_code == 200
    assert job_resp_2.json()["job_id"] == job_id

    exports_after_second_call = db_session.query(Export).filter_by(job_id=job_id).all()
    assert len(exports_after_second_call) == 2  # unchanged — no duplicate GPU/CPU work
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-api pytest apps/api/tests/test_e2e_pipeline.py -v`
Expected: FAIL initially only if any prior task was skipped or miswired — since Tasks 1-17 already deliver every piece this test exercises, treat any failure here as a signal to revisit the specific task/step it points at, not as an expected new-code gap. Re-run after fixing.

- [ ] **Step 3: Make the test pass**

If Steps 1-17 were followed exactly, no new production code should be needed — this step is verification, not implementation. If it fails, the most likely gaps are: `aura-api`'s test extras missing `test-fixtures`/`aura-worker` as workspace dependencies (add them to `apps/api/pyproject.toml`'s `[project.optional-dependencies].test`), or the `ffmpeg`/`ffprobe` binaries missing from the test environment `PATH`.

Run: `uv run --package aura-api pytest apps/api/tests/test_e2e_pipeline.py -v`
Expected: PASS

- [ ] **Step 4: Run the entire test suite**

Run: `make test`
Expected: every package's suite passes.

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/test_e2e_pipeline.py apps/api/pyproject.toml
git commit -m "test: add end-to-end pipeline test covering the Phase 1 exit criterion"
```

---

## Task 19: Containerize API and worker

**Files:**
- Create: `apps/api/Dockerfile`
- Create: `workers/transcription/Dockerfile`
- Modify: `infra/docker-compose.yml`

**Interfaces:**
- Produces: `aura-api:local` and `aura-worker:local` images; `docker compose up` runs the full stack (Postgres, Redis, MinIO, API, worker) for a manual smoke test.

- [ ] **Step 1: Write `apps/api/Dockerfile`**

```dockerfile
FROM python:3.11-slim AS base

RUN useradd --create-home --uid 1000 appuser
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY packages/score_schema /app/packages/score_schema
COPY apps/api /app/apps/api

RUN uv sync --package aura-api --no-dev

USER appuser
EXPOSE 8000
CMD ["uv", "run", "--package", "aura-api", "uvicorn", "aura_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Write `workers/transcription/Dockerfile`**

```dockerfile
FROM python:3.11-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 appuser
RUN pip install --no-cache-dir uv

WORKDIR /app
COPY pyproject.toml /app/pyproject.toml
COPY packages/score_schema /app/packages/score_schema
COPY packages/musicxml /app/packages/musicxml
COPY apps/api /app/apps/api
COPY workers/transcription /app/workers/transcription

RUN uv sync --package aura-worker --no-dev

USER appuser
CMD ["uv", "run", "--package", "aura-worker", "rq", "worker", "transcription", "--url", "$REDIS_URL"]
```

- [ ] **Step 3: Extend `infra/docker-compose.yml` with `api` and `worker` services**

```yaml
# infra/docker-compose.yml (append under services:)
  api:
    build:
      context: ../
      dockerfile: apps/api/Dockerfile
    env_file: .env.example
    environment:
      DATABASE_URL: postgresql+psycopg2://aura:aura@postgres:5432/aura
      REDIS_URL: redis://redis:6379/0
      S3_ENDPOINT_URL: http://minio:9000
    ports: ["8000:8000"]
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio-init: { condition: service_completed_successfully }

  worker:
    build:
      context: ../
      dockerfile: workers/transcription/Dockerfile
    env_file: .env.example
    environment:
      DATABASE_URL: postgresql+psycopg2://aura:aura@postgres:5432/aura
      REDIS_URL: redis://redis:6379/0
      S3_ENDPOINT_URL: http://minio:9000
    depends_on:
      postgres: { condition: service_healthy }
      redis: { condition: service_healthy }
      minio-init: { condition: service_completed_successfully }
```

- [ ] **Step 4: Verify the full stack builds and boots**

Run: `docker compose -f infra/docker-compose.yml up --build -d && sleep 5 && curl -f http://localhost:8000/healthz`
Expected: `{"status":"ok"}`; `docker compose -f infra/docker-compose.yml logs worker` shows the RQ worker connected and listening on the `transcription` queue with no errors.

- [ ] **Step 5: Run a manual end-to-end smoke test against the containerized stack**

Run (from repo root, with `infra` stack up):
```bash
uv run --package test-fixtures python -c "
from pathlib import Path
from test_fixtures.generate import write_guitar_pluck_wav
write_guitar_pluck_wav(Path('/tmp/riff.wav'), duration_s=3.0)
"
UPLOAD=$(curl -s -X POST http://localhost:8000/v1/uploads \
  -H 'Content-Type: application/json' \
  -d '{"filename":"riff.wav","content_type":"audio/wav"}')
echo "$UPLOAD"
```
Expected: a JSON response with `object_key` and `upload_url`; follow with a `PUT` of `/tmp/riff.wav` to `upload_url`, then `POST /v1/projects`, `POST /v1/projects/{id}/transcriptions`, and poll `GET /v1/jobs/{id}` until `status: succeeded` — this is the manual equivalent of Task 18's automated test, run once against real containers as a final sanity check before calling Phase 1 done.

- [ ] **Step 6: Commit**

```bash
git add apps/api/Dockerfile workers/transcription/Dockerfile infra/docker-compose.yml
git commit -m "chore: containerize api and worker services"
```

---

## Definition of Done for this plan

Matches `ARCHITECTURE.md` §10 Phase 1 exit criterion exactly:

- [ ] A developer can upload a fixed guitar or piano fixture and receive deterministic MIDI/MusicXML twice without duplicate GPU/CPU processing (Task 18).
- [ ] Integration tests exercise the full flow (Task 18).
- [ ] Structured stage progress and errors are visible via `GET /v1/jobs/{id}` (Task 9).
- [ ] `make test` and `docker compose up --build` both succeed from a clean checkout (Tasks 18-19).

Everything past this point — beat/meter estimation, guitar string/fret and piano hand assignment, the web client and SVG preview, PDF export, auth/quotas, and security/observability hardening — is Phase 2+ and belongs in its own plan(s), per the scope note above.
