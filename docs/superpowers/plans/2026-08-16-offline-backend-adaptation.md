# Offline Backend Adaptation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The API process runs as a single local Python process with zero external services — no Postgres, no Redis, no S3/MinIO, no Docker required — producing identical transcription output to today's stack.

**Architecture:** Four independent swaps, each minimal-diff by design (verified against every real call site before writing this plan, not guessed):
1. **DB** — `DATABASE_URL` becomes a `sqlite:///` URL; `aura_api/db.py` gains a conditional `check_same_thread: False`. Zero model changes (models already use portable `String`/`JSON` columns).
2. **Storage** — `aura_api/storage.py`'s `StorageClient` (boto3/S3) becomes `LocalStorageClient` (plain filesystem under `AURA_DATA_DIR/blobs/`), **keeping the exact same method names** (`put_bytes`, `get_bytes`, `download_media_asset`, `head_object`) used by every worker stage and `projects.py` today — so those call sites need **zero changes**. `workers/transcription/src/aura_worker/storage.py` is deleted outright; `runner.py` imports `LocalStorageClient` from `aura_api.storage` instead (the dependency direction worker→api already exists for `db`/`models`).
3. **Upload/download HTTP surface** — `POST /v1/uploads` becomes a direct `multipart/form-data` upload (no presigned URL). `GET /v1/exports/{id}/download` is a new route serving the file directly (`FileResponse`); `ExportStatusResponse.download_url` now holds a local relative path instead of a presigned S3 URL.
4. **Job dispatch** — `aura_api/queue.py` submits to a single-worker `ThreadPoolExecutor` instead of enqueuing to Redis/RQ. `run_transcription_job(job_id)` (in `aura_worker/runner.py`) is already shaped exactly right for this — it opens its own session and storage client per call and takes only a `job_id` string — so **its signature and internals don't change**.

**Tech Stack:** Python standard library only for the new pieces (`concurrent.futures.ThreadPoolExecutor`, `pathlib`/`shutil` for local storage, FastAPI's `UploadFile`/`FileResponse`). One new dependency: `python-multipart` (required by FastAPI to parse multipart bodies, not previously needed since no endpoint parsed one).

**Spec:** `docs/superpowers/specs/2026-08-16-offline-backend-adaptation-design.md`

## Global Constraints

- **No transcription algorithm changes.** `probe.py`, `normalize.py`, `inference.py`, `structure.py`, `quantize.py`, `assign.py`, and the stage-runner `export.py` are not touched by this plan (verified: none of their `ctx.storage.*` call sites use a method whose *name* is changing — only `aura_api/storage.py`'s S3-specific methods `presign_put`/`presign_get`/`head_object`'s *implementation* change, and `head_object`'s name is kept specifically so `projects.py` needs zero edits).
- **SQLite must be file-backed, not `:memory:`, in tests.** Verified: `aura_worker/runner.py`'s `_SessionLocal = sessionmaker(bind=get_engine())` is a **module-level** global bound once at import time via `aura_api.db.get_engine()`. In the e2e test, this is a *different* engine object than the one the test's `db_session` fixture creates via its own `create_engine(os.environ["DATABASE_URL"])` call — with Postgres today, both point at the same real server so they see each other's writes. An in-memory SQLite URL would give each engine its own isolated database, silently breaking the e2e test (the worker's session would never see the job the test created). A shared temp **file** path avoids this entirely, matching current behavior.
- **`put_file`/`get_file` are dropped, not ported.** Verified via full-repo grep: no stage file, router, or test calls them. Only `download_media_asset`, `put_bytes`, `get_bytes`, and `head_object` have real callers.
- **`CreateUploadRequest` is deleted, not modified.** The multipart upload takes `filename`/`content_type` from the uploaded `UploadFile` itself; a separate JSON request body model no longer applies.
- **`ExportStatusResponse`'s shape is unchanged** (`download_url: str | None`) — only what the string *contains* changes (a local path instead of a presigned URL), so `routers/jobs.py` and any other consumer of that schema needs no changes.
- **`.envrc` is real dev-environment config, not just test config** — it currently exports `DATABASE_URL`/`REDIS_URL`/`S3_*` for local `uvicorn` runs. It must be updated in this plan, or a developer running the app locally (outside pytest) breaks even though tests pass.
- **`workers/transcription/pyproject.toml` also lists `boto3`/`rq`/`redis`/`psycopg2-binary` as direct deps** (not just `apps/api`'s) — both files need the cleanup, verified via direct read of both.
- **`ExportStatusResponse.download_url` route path is `/v1/exports/{id}/download`** — a new, separate route from the existing `GET /v1/exports/{id}` status route (which keeps returning JSON). The e2e test's `urllib.request.urlopen(download_url)` calls are replaced with `client.get(download_url)` (FastAPI's `TestClient`/httpx ASGI transport, not real sockets) since there's no real HTTP server for a bare `urllib` call to reach once S3 is gone — the download route is fetched through the same in-process test client as everything else.

## File Structure

```text
apps/api/src/aura_api/
  config.py           # Modify: drop redis_url/s3_*, add data_dir
  db.py                # Modify: sqlite connect_args
  storage.py            # Modify: StorageClient(boto3) -> LocalStorageClient(filesystem)
  queue.py              # Modify: Redis/RQ -> ThreadPoolExecutor
  schemas.py             # Modify: CreateUploadRequest deleted, CreateUploadResponse
                          #   drops upload_url
  routers/
    uploads.py            # Modify: multipart upload handler
    exports.py             # Modify: add /download route, presign_get -> local path
apps/api/tests/
  conftest.py             # Modify: DATABASE_URL default -> sqlite temp file
  test_e2e_pipeline.py     # Modify: upload/download flow, drop s3_client fixture

workers/transcription/src/aura_worker/
  storage.py              # Delete: consolidated into aura_api.storage
  runner.py                # Modify: import LocalStorageClient from aura_api.storage
workers/transcription/tests/
  conftest.py               # Modify: DATABASE_URL default -> sqlite temp file

apps/api/pyproject.toml       # Modify: drop boto3/rq/redis/psycopg2-binary, add python-multipart
workers/transcription/pyproject.toml  # Modify: drop boto3/rq/redis/psycopg2-binary

.envrc                        # Modify: sqlite + data dir, drop redis/s3 vars
```

---

## Task 1: SQLite database support

**Files:**
- Modify: `apps/api/src/aura_api/db.py`
- Modify: `apps/api/tests/conftest.py`
- Modify: `workers/transcription/tests/conftest.py`
- Modify: `.envrc`

**Interfaces:**
- Produces: `get_engine()` (unchanged signature) works against a `sqlite:///` URL as well as `postgresql+...` — adds `check_same_thread: False` only for sqlite, since the thread-pool job runner (Task 6) opens its own session from a worker thread.

- [ ] **Step 1: Write the failing test**

Add to `apps/api/tests/test_db.py` (new file):

```python
import os

from sqlalchemy import text

from aura_api.db import get_engine


def test_get_engine_connects_against_sqlite_url(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    engine = get_engine()
    with engine.connect() as conn:
        assert conn.execute(text("select 1")).scalar() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_db.py -v`
Expected: currently this actually likely PASSES already (sqlite works with SQLAlchemy out of the box even without `check_same_thread`, since this test uses the engine from the same thread it was created on) — this is a smoke test for the URL scheme, not yet proof of the threading fix. Run it to confirm a baseline pass, then proceed; the real threading behavior is exercised by Task 6's test instead. If it fails for any other reason (e.g. `KeyError: 'DATABASE_URL'`), investigate before continuing — `os.environ["DATABASE_URL"]` in `get_engine()` requires the env var to be set, and `monkeypatch.setenv` in this test handles that.

- [ ] **Step 3: Update `get_engine()`**

In `apps/api/src/aura_api/db.py`, replace `get_engine`:

```python
def get_engine():
    url = os.environ["DATABASE_URL"]
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)
```

- [ ] **Step 4: Run test to verify it still passes**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_db.py -v`
Expected: PASS.

- [ ] **Step 5: Switch both test conftests to a shared sqlite file, not Postgres**

In `apps/api/tests/conftest.py`, replace the top of the file:

```python
import os
import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

_TEST_DB_PATH = Path(tempfile.gettempdir()) / "aura_api_test.db"
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TEST_DB_PATH}")

from aura_api.db import Base  # noqa: E402


@pytest.fixture()
def db_session():
    engine = create_engine(os.environ["DATABASE_URL"], connect_args={"check_same_thread": False})
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

Apply the identical change to `workers/transcription/tests/conftest.py` (same `_TEST_DB_PATH`/`os.environ.setdefault`/`connect_args` edit; that file's `sample_job` and `workdir` fixtures below it are untouched). Use a **different** filename for that conftest's `_TEST_DB_PATH` (e.g. `aura_worker_test.db`) — the two test suites run as separate `uv run --package ...` processes per the root `Makefile`, so they never need to share a file, and using distinct names avoids any accidental cross-suite file collision if both happen to run around the same time on a shared machine.

- [ ] **Step 6: Run both test suites to verify no regression**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests -v` and `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests -v`
Expected: PASS — every test that was passing against Postgres now passes against sqlite. (The e2e tests will still fail at this point because they still exercise the old S3 upload/download flow — that's expected until Task 8. Everything else should be green.)

- [ ] **Step 7: Update `.envrc` for real local runs**

Replace `.envrc`'s contents:

```bash
export DATABASE_URL=sqlite:///./data/aura.db
export AURA_DATA_DIR=./data
```

(`REDIS_URL`/`S3_*` lines removed entirely.)

- [ ] **Step 8: Commit**

```bash
git add apps/api/src/aura_api/db.py apps/api/tests/conftest.py apps/api/tests/test_db.py workers/transcription/tests/conftest.py .envrc
git commit -m "feat(db): switch from Postgres to SQLite"
```

---

## Task 2: `LocalStorageClient` and config

**Files:**
- Modify: `apps/api/src/aura_api/config.py`
- Modify: `apps/api/src/aura_api/storage.py`

**Interfaces:**
- Produces: `LocalStorageClient` with `put_bytes(key, data)`, `get_bytes(key) -> bytes`, `download_media_asset(key, dest_path) -> Path`, `head_object(key) -> dict | None`, and a new `path_for(key) -> Path` (used by Task 5's download route). Method names/signatures for the first four are **identical** to the current `WorkerStorageClient`/`StorageClient` methods they replace, so every existing caller (worker stages, `projects.py`) keeps working unmodified.
- Consumes: `settings.data_dir` (new config field).

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_storage.py`:

```python
import pytest

from aura_api.storage import LocalStorageClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config

    monkeypatch.setattr(config, "settings", config.Settings())
    import aura_api.storage as storage_module

    monkeypatch.setattr(storage_module, "settings", config.settings)
    return LocalStorageClient()


def test_put_then_get_bytes_round_trips(client):
    client.put_bytes("jobs/1/a.json", b"hello")
    assert client.get_bytes("jobs/1/a.json") == b"hello"


def test_put_bytes_creates_parent_directories(client, tmp_path):
    client.put_bytes("a/b/c/d.bin", b"x")
    assert (tmp_path / "blobs" / "a" / "b" / "c" / "d.bin").is_file()


def test_download_media_asset_copies_to_dest(client, tmp_path):
    client.put_bytes("uploads/x/riff.wav", b"audio-bytes")
    dest = tmp_path / "work" / "input"
    result = client.download_media_asset("uploads/x/riff.wav", dest)
    assert result == dest
    assert dest.read_bytes() == b"audio-bytes"


def test_head_object_returns_content_length_for_existing_key(client):
    client.put_bytes("uploads/x/riff.wav", b"12345")
    head = client.head_object("uploads/x/riff.wav")
    assert head == {"ContentLength": 5}


def test_head_object_returns_none_for_missing_key(client):
    assert client.head_object("does/not/exist") is None


def test_path_for_returns_filesystem_path_under_blob_root(client, tmp_path):
    assert client.path_for("a/b.mid") == tmp_path / "blobs" / "a" / "b.mid"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_storage.py -v`
Expected: FAIL — `LocalStorageClient` doesn't exist yet (current `storage.py` only has `StorageClient`, boto3-backed).

- [ ] **Step 3: Update `config.py`**

Replace the `Settings` class body:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    database_url: str
    data_dir: str = "./data"
    max_upload_bytes: int = 500 * 1024 * 1024
    max_duration_ms: int = 15 * 60 * 1000
```

(`redis_url`, `s3_endpoint_url`, `s3_access_key`, `s3_secret_key`, `s3_bucket`, `s3_region` all removed.)

- [ ] **Step 4: Replace `storage.py`**

```python
from __future__ import annotations

import shutil
from pathlib import Path

from aura_api.config import settings


class LocalStorageClient:
    def __init__(self) -> None:
        self.root = Path(settings.data_dir) / "blobs"

    def path_for(self, key: str) -> Path:
        return self.root / key

    def put_bytes(self, key: str, data: bytes) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def get_bytes(self, key: str) -> bytes:
        return self.path_for(key).read_bytes()

    def download_media_asset(self, key: str, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.path_for(key), dest_path)
        return dest_path

    def head_object(self, key: str) -> dict | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        return {"ContentLength": path.stat().st_size}


storage_client = LocalStorageClient()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_storage.py -v`
Expected: PASS (6 tests).

- [ ] **Step 6: Run the full `aura-api` suite**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests -v`
Expected: `routers/projects.py`'s `head_object` call site keeps working unmodified (same method name/return shape). Upload/export/e2e tests still fail at this point (expected — Tasks 4/5/8 fix those). No other regressions.

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/aura_api/config.py apps/api/src/aura_api/storage.py apps/api/tests/test_storage.py
git commit -m "feat(storage): replace S3 client with LocalStorageClient"
```

---

## Task 3: Worker storage consolidation

**Files:**
- Delete: `workers/transcription/src/aura_worker/storage.py`
- Modify: `workers/transcription/src/aura_worker/runner.py`

**Interfaces:**
- Produces: `run_transcription_job` (unchanged signature) now builds its `StageContext` with `aura_api.storage.LocalStorageClient()` instead of the deleted `WorkerStorageClient`.
- Consumes: `LocalStorageClient` (Task 2).

- [ ] **Step 1: Delete the file**

```bash
git rm workers/transcription/src/aura_worker/storage.py
```

- [ ] **Step 2: Update `runner.py`'s import and instantiation**

In `workers/transcription/src/aura_worker/runner.py`, change:

```python
from aura_worker.storage import WorkerStorageClient
```
to
```python
from aura_api.storage import LocalStorageClient
```

and change:
```python
    storage = WorkerStorageClient()
```
to
```python
    storage = LocalStorageClient()
```

No other line in this file changes — every stage call (`probe.run(ctx)`, `normalize.run(ctx, ...)`, etc.) is untouched, since `ctx.storage.get_bytes`/`put_bytes`/`download_media_asset` are called by name through `ctx`, not through a type-specific reference.

- [ ] **Step 3: Run the full worker suite**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests -v`
Expected: PASS — every stage test (`test_probe.py`, `test_normalize.py`, `test_inference.py`, `test_structure.py`, `test_quantize.py`, `test_assign.py`, `test_export.py`, `test_piano_hands.py`, `test_fingering.py`) already builds its own `StageContext` with a test double storage (`FakeStorage`, per the piano/guitar sub-project pattern) rather than `WorkerStorageClient` directly — confirm this by checking one test file's imports if any failure is surprising, but expect zero changes needed there since none of them import the now-deleted module.

- [ ] **Step 4: Commit**

```bash
git add -A workers/transcription/src/aura_worker/runner.py
git commit -m "refactor(worker): consolidate storage into aura_api.storage.LocalStorageClient"
```

---

## Task 4: Direct multipart upload

**Files:**
- Modify: `apps/api/src/aura_api/schemas.py`
- Modify: `apps/api/src/aura_api/routers/uploads.py`
- Modify: `apps/api/pyproject.toml`

**Interfaces:**
- Produces: `POST /v1/uploads` now accepts `multipart/form-data` (a single `file` field) instead of a JSON body, and returns `{"object_key": "..."}` (no `upload_url`).
- Consumes: `storage_client.put_bytes` (Task 2).

- [ ] **Step 1: Add `python-multipart` to `apps/api/pyproject.toml`**

In the `dependencies` list, add `"python-multipart>=0.0.9",` (alongside the existing `fastapi`/`uvicorn` lines). Then run `uv sync --package aura-api` to install it before continuing — the next steps' tests will fail with a FastAPI runtime error (not an import error) if this is skipped, since multipart parsing is dispatched lazily by Starlette.

- [ ] **Step 2: Write the failing test**

Create `apps/api/tests/test_uploads.py`:

```python
import io

from fastapi.testclient import TestClient

from aura_api.main import create_app


def test_upload_accepts_multipart_file_and_returns_object_key(tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.uploads as uploads_module

    monkeypatch.setattr(uploads_module, "storage_client", storage.storage_client)

    client = TestClient(create_app())
    resp = client.post(
        "/v1/uploads",
        files={"file": ("riff.wav", io.BytesIO(b"fake-audio-bytes"), "audio/wav")},
    )
    assert resp.status_code == 201
    object_key = resp.json()["object_key"]
    assert object_key.startswith("uploads/")
    assert storage.storage_client.get_bytes(object_key) == b"fake-audio-bytes"


def test_upload_rejects_unsupported_content_type():
    client = TestClient(create_app())
    resp = client.post(
        "/v1/uploads",
        files={"file": ("evil.exe", io.BytesIO(b"x"), "application/x-msdownload")},
    )
    assert resp.status_code == 422
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_uploads.py -v`
Expected: FAIL — current `create_upload` still expects a JSON body (`CreateUploadRequest`), so a multipart POST returns 422 for the wrong reason (missing `filename`/`content_type` fields), and the response has no `object_key` written to real storage.

- [ ] **Step 4: Update `schemas.py`**

Delete `CreateUploadRequest` entirely. Replace `CreateUploadResponse`:

```python
class CreateUploadResponse(BaseModel):
    object_key: str
```

`_ALLOWED_CONTENT_TYPES` and `make_upload_object_key` stay exactly as-is (still used, now by the router directly instead of by a validator).

- [ ] **Step 5: Rewrite `uploads.py`**

```python
from fastapi import APIRouter, File, HTTPException, UploadFile

from aura_api.schemas import _ALLOWED_CONTENT_TYPES, CreateUploadResponse, make_upload_object_key
from aura_api.storage import storage_client

router = APIRouter(tags=["uploads"])


@router.post("/uploads", response_model=CreateUploadResponse, status_code=201)
async def create_upload(file: UploadFile = File(...)) -> CreateUploadResponse:
    if file.content_type not in _ALLOWED_CONTENT_TYPES:
        raise HTTPException(status_code=422, detail=f"unsupported content_type: {file.content_type}")
    object_key = make_upload_object_key(file.filename)
    data = await file.read()
    storage_client.put_bytes(object_key, data)
    return CreateUploadResponse(object_key=object_key)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_uploads.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/aura_api/schemas.py apps/api/src/aura_api/routers/uploads.py apps/api/pyproject.toml
git commit -m "feat(api): replace presigned S3 upload with direct multipart upload"
```

---

## Task 5: Direct export download route

**Files:**
- Modify: `apps/api/src/aura_api/routers/exports.py`

**Interfaces:**
- Produces: `GET /v1/exports/{id}` keeps returning JSON status (`download_url` now a relative path like `/v1/exports/{id}/download` instead of a presigned S3 URL). New `GET /v1/exports/{id}/download` streams the file directly via `FileResponse`.
- Consumes: `storage_client.path_for` (Task 2).

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_exports_download.py`:

```python
from fastapi.testclient import TestClient

from aura_api.main import create_app
from aura_api.models import Export, MediaAsset, Project, TranscriptionJob


def _make_succeeded_export(db_session, storage_client, object_key: str, data: bytes) -> str:
    storage_client.put_bytes(object_key, data)
    project = Project(owner_id="anonymous", title="T", instrument="guitar")
    db_session.add(project)
    db_session.flush()
    asset = MediaAsset(project_id=project.id, kind="source", object_key="uploads/x/riff.wav")
    db_session.add(asset)
    db_session.flush()
    job = TranscriptionJob(project_id=project.id, media_asset_id=asset.id, input_hash="h", status="succeeded")
    db_session.add(job)
    db_session.flush()
    export = Export(project_id=project.id, job_id=job.id, format="midi", status="succeeded", object_key=object_key)
    db_session.add(export)
    db_session.commit()
    return export.id


def test_export_status_download_url_is_a_local_route(db_session, tmp_path, monkeypatch):
    monkeypatch.setenv("AURA_DATA_DIR", str(tmp_path))
    from aura_api import config, storage

    monkeypatch.setattr(config, "settings", config.Settings())
    monkeypatch.setattr(storage, "settings", config.settings)
    monkeypatch.setattr(storage, "storage_client", storage.LocalStorageClient())
    import aura_api.routers.exports as exports_module

    monkeypatch.setattr(exports_module, "storage_client", storage.storage_client)

    export_id = _make_succeeded_export(db_session, storage.storage_client, "jobs/1/exports/out.mid", b"MThd-fake")

    client = TestClient(create_app())
    status_resp = client.get(f"/v1/exports/{export_id}")
    assert status_resp.status_code == 200
    download_url = status_resp.json()["download_url"]
    assert download_url == f"/v1/exports/{export_id}/download"

    download_resp = client.get(download_url)
    assert download_resp.status_code == 200
    assert download_resp.content == b"MThd-fake"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_exports_download.py -v`
Expected: FAIL — `storage_client.presign_get` no longer exists (it was dropped along with `StorageClient` in Task 2), so the current `get_export` handler raises `AttributeError`.

- [ ] **Step 3: Rewrite `exports.py`**

```python
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from aura_api.deps import get_db
from aura_api.models import Export
from aura_api.schemas import ExportStatusResponse
from aura_api.storage import storage_client

router = APIRouter(tags=["exports"])

_MEDIA_TYPES = {"midi": "audio/midi", "musicxml": "application/xml"}


@router.get("/exports/{export_id}", response_model=ExportStatusResponse)
def get_export(export_id: str, db: Session = Depends(get_db)) -> ExportStatusResponse:
    export = db.get(Export, export_id)
    if export is None:
        raise HTTPException(status_code=404, detail="export not found")

    download_url = None
    if export.status == "succeeded" and export.object_key:
        download_url = f"/v1/exports/{export.id}/download"

    return ExportStatusResponse(
        id=export.id, format=export.format, status=export.status, download_url=download_url
    )


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, db: Session = Depends(get_db)) -> FileResponse:
    export = db.get(Export, export_id)
    if export is None or export.status != "succeeded" or not export.object_key:
        raise HTTPException(status_code=404, detail="export not available")

    path = storage_client.path_for(export.object_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="export file missing")

    return FileResponse(
        path,
        media_type=_MEDIA_TYPES.get(export.format, "application/octet-stream"),
        filename=path.name,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_exports_download.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/api/src/aura_api/routers/exports.py apps/api/tests/test_exports_download.py
git commit -m "feat(api): serve exports directly instead of presigned S3 URLs"
```

---

## Task 6: In-process thread-pool job dispatch

**Files:**
- Modify: `apps/api/src/aura_api/queue.py`
- Modify: `apps/api/pyproject.toml`
- Modify: `workers/transcription/pyproject.toml`

**Interfaces:**
- Produces: `enqueue_transcription_job(job_id: str) -> None` (unchanged signature) submits to a module-level single-worker `ThreadPoolExecutor` instead of an RQ queue.
- Consumes: `run_transcription_job` (unchanged, `aura_worker.runner`).

- [ ] **Step 1: Write the failing test**

Create `apps/api/tests/test_queue.py`:

```python
import threading
import time

from aura_api.queue import enqueue_transcription_job


def test_enqueue_runs_target_function_in_background_thread(monkeypatch):
    calls = []
    done = threading.Event()

    def fake_run(job_id: str) -> None:
        calls.append((job_id, threading.current_thread() is not threading.main_thread()))
        done.set()

    import aura_api.queue as queue_module

    monkeypatch.setattr(queue_module, "run_transcription_job", fake_run)

    enqueue_transcription_job("job-123")
    assert done.wait(timeout=2.0), "job did not run within timeout"
    assert calls == [("job-123", True)]


def test_enqueue_serializes_two_jobs_without_dropping_either(monkeypatch):
    order = []
    lock = threading.Lock()

    def fake_run(job_id: str) -> None:
        time.sleep(0.05)
        with lock:
            order.append(job_id)

    import aura_api.queue as queue_module

    monkeypatch.setattr(queue_module, "run_transcription_job", fake_run)

    enqueue_transcription_job("a")
    enqueue_transcription_job("b")
    time.sleep(0.3)
    assert sorted(order) == ["a", "b"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_queue.py -v`
Expected: FAIL — `aura_api.queue` still imports `redis`/`rq` and has no `run_transcription_job` name to monkeypatch (it's currently referenced only as a string, `"aura_worker.runner.run_transcription_job"`, passed to RQ — not imported directly).

- [ ] **Step 3: Rewrite `queue.py`**

```python
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor

from aura_worker.runner import run_transcription_job

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)


def enqueue_transcription_job(job_id: str) -> None:
    future = _executor.submit(run_transcription_job, job_id)
    future.add_done_callback(_log_unexpected_failure)


def _log_unexpected_failure(future) -> None:
    exc = future.exception()
    if exc is not None:
        logger.error("transcription job raised an unhandled exception", exc_info=exc)
```

`run_transcription_job` already wraps its own body in `try`/`except JobFailure`/`except Exception` and writes `error_code`/`error_detail` to the job row before any exception could escape (per the spec's Error Handling section) — the `add_done_callback` here is a defensive backstop only, so a truly unexpected exception is logged instead of silently vanishing into an unobserved `Future`, not a primary error-handling path.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_queue.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Remove `boto3`/`rq`/`redis`/`psycopg2-binary` from both `pyproject.toml` files**

In `apps/api/pyproject.toml`'s `dependencies`, delete the `"boto3>=1.34"`, `"rq>=1.16"`, `"redis>=5.0"`, `"psycopg2-binary>=2.9"` lines (leave `python-multipart` from Task 4 in place).

In `workers/transcription/pyproject.toml`'s `dependencies`, delete the same four lines (`boto3`, `rq`, `redis`, `psycopg2-binary`).

Run `uv sync --all-packages` after editing both files to update the lockfile.

- [ ] **Step 6: Run the full `aura-api` suite**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests -v`
Expected: PASS except the e2e tests (still pending Task 8's rewrite).

- [ ] **Step 7: Commit**

```bash
git add apps/api/src/aura_api/queue.py apps/api/pyproject.toml workers/transcription/pyproject.toml uv.lock
git commit -m "feat(api): replace Redis/RQ with an in-process thread pool"
```

---

## Task 7: `POST /v1/projects` object-key lookup regression check

**Files:**
- None modified — verification only (this task exists because `projects.py`'s `storage_client.head_object(...)` call was deliberately left untouched in Task 2, and deserves a direct check rather than only incidental coverage).

**Interfaces:** none new.

- [ ] **Step 1: Run the existing `projects.py` tests**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests -k project -v`
Expected: PASS — confirms `head_object`'s name-preserving replacement (Task 2) didn't silently break the 404-on-missing-upload path or the `bytes`/`ContentLength` propagation into `MediaAsset.bytes`. If this file doesn't exist yet as a dedicated test (check `apps/api/tests/test_projects.py`), add one covering: (a) `POST /v1/projects` with a real, previously-uploaded `object_key` succeeds and stores `MediaAsset.bytes` matching the uploaded size; (b) `POST /v1/projects` with a nonexistent `object_key` returns 404. Use the same `LocalStorageClient`-via-`monkeypatch` pattern as Task 4/5's tests if writing new coverage.

- [ ] **Step 2: Commit only if a new test file was added**

```bash
git add apps/api/tests/test_projects.py  # only if created
git commit -m "test(api): cover project creation against LocalStorageClient head_object"
```

(Skip this commit if `test_projects.py` already existed and passed without changes.)

---

## Task 8: End-to-end test migration

**Files:**
- Modify: `apps/api/tests/test_e2e_pipeline.py`

**Interfaces:**
- Consumes: multipart upload (Task 4), download route (Task 5), thread-pool dispatch (Task 6) — though the e2e test continues to call `run_transcription_job` directly and synchronously (as it does today, standing in for real dispatch), since asserting on thread-pool timing in an e2e test would be flaky; Task 6's own tests already cover the threading behavior directly.
- Produces: both existing e2e tests (guitar idempotency, piano grand staff) pass with zero S3 dependency.

- [ ] **Step 1: Remove the `s3_client` fixture and `boto3` import**

At the top of `apps/api/tests/test_e2e_pipeline.py`, delete:
```python
import boto3
```
and delete the whole `s3_client` fixture:
```python
@pytest.fixture()
def s3_client():
    return boto3.client(...)
```

- [ ] **Step 2: Replace the upload step in both tests**

In both `test_full_pipeline_upload_to_export_is_idempotent` and `test_full_pipeline_piano_renders_grand_staff`, replace:

```python
    upload_resp = client.post(
        "/v1/uploads", json={"filename": "riff.wav", "content_type": "audio/wav"}
    )
    assert upload_resp.status_code == 201
    object_key = upload_resp.json()["object_key"]

    s3_client.put_object(Bucket=settings.s3_bucket, Key=object_key, Body=fixture_path.read_bytes())
```

with:

```python
    with fixture_path.open("rb") as f:
        upload_resp = client.post(
            "/v1/uploads", files={"file": (fixture_path.name, f, "audio/wav")}
        )
    assert upload_resp.status_code == 201
    object_key = upload_resp.json()["object_key"]
```

(adjusting the filename literal per test — `"riff.wav"`/`fixture_path.name` are already the same value in both call sites, since `fixture_path` is already named `riff.wav`/`melody.wav` respectively earlier in each test). Remove the now-unused `s3_client` parameter from both test function signatures, and remove `from aura_api.config import settings` if nothing else in the file uses `settings` after this change (check the rest of the file first — it doesn't, per the full file read used to write this plan).

- [ ] **Step 3: Replace the download step in both tests**

Every occurrence of:
```python
    import urllib.request

    with urllib.request.urlopen(download_url) as f:
        midi_bytes = f.read()
```
(and the equivalent `musicxml_bytes`/`musicxml_download_url` block) becomes:
```python
    download_resp = client.get(download_url)
    assert download_resp.status_code == 200
    midi_bytes = download_resp.content
```
(substitute `musicxml_bytes`/`musicxml_download_url` variable names in the second occurrence within the first test, and the third occurrence in the piano test). Remove the now-redundant `import urllib.request` lines.

- [ ] **Step 4: Run both e2e tests**

Run: `uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_e2e_pipeline.py -v`
Expected: PASS (2 tests) — guitar idempotency and piano grand-staff, both now running with zero S3/boto3 involvement, entirely against local SQLite + local filesystem storage + in-process thread-pool dispatch (Task 6's `enqueue_transcription_job` isn't actually called by these tests — they call `run_transcription_job` directly, same as before — so this is really proving the storage/DB/HTTP-surface changes, while Task 6's own tests prove the dispatch change).

- [ ] **Step 5: Commit**

```bash
git add apps/api/tests/test_e2e_pipeline.py
git commit -m "test(api): migrate e2e pipeline tests off S3/boto3"
```

---

## Task 9: Full offline workspace verification

**Files:**
- None created or modified — verification only.

- [ ] **Step 1: Confirm no local infra is running**

Run `docker compose -f /home/user/AuraAudio/infra/docker-compose.yml down` (or confirm via `docker ps` that no Postgres/Redis/MinIO containers from this project are up). This is the actual proof of the sub-project's goal — if `make test` only passes because `infra/docker-compose.yml` happens to still be running, nothing has actually changed.

- [ ] **Step 2: Run the full workspace test suite with infra down**

Run: `source /home/user/AuraAudio/.envrc && cd /home/user/AuraAudio && make test`
Expected: every package's suite passes (score-schema, musicxml, test-fixtures, aura-api, aura-worker) — including both e2e tests — with zero Postgres/Redis/MinIO containers running.

- [ ] **Step 3: Manual smoke test — run the API standalone**

Start the API with `uv run --package aura-api uvicorn aura_api.main:app --reload` (no other process running). Through `curl` or the FastAPI `/docs` page: upload a fixture file via multipart to `POST /v1/uploads`, create a project, start a transcription, poll `GET /v1/jobs/{id}` until `succeeded`, then `GET /v1/exports/{id}` and follow the returned `download_url` to confirm the MusicXML/MIDI bytes download correctly — all through one process, no other services running.

- [ ] **Step 4: Update `docs/superpowers/SESSION-HANDOFF.md`**

Mark sub-project 1 (offline backend adaptation) done, matching the existing "Phase 2 backend sub-projects" section's style for prior completed sub-projects. Point at sub-project 2 (Tauri shell + packaging) as next.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/SESSION-HANDOFF.md
git commit -m "docs: mark offline backend adaptation sub-project done"
```

## Definition of Done

- `make test` passes with `infra/docker-compose.yml` **not running** — no Postgres, Redis, or MinIO.
- A developer can run the API process standalone, upload a fixture via multipart, create a project, start a transcription, poll it to completion, and download the MusicXML/MIDI export — all through one local Python process.
- Guitar and piano end-to-end fixture outputs are unchanged (same structural assertions passing as before this sub-project).
- `boto3`, `rq`, `redis`, `psycopg2-binary` no longer appear in either package's dependencies; `python-multipart` is present in `apps/api`.
- `docs/superpowers/SESSION-HANDOFF.md` records this sub-project done and points at sub-project 2 next.
