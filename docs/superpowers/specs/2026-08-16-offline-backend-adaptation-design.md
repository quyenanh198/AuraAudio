# Offline Backend Adaptation — Design

## Context

The project is pivoting from `ARCHITECTURE.md`'s cloud-service model (web client,
Postgres, S3, Redis-queued worker) to a fully offline desktop app — see
`docs/superpowers/SESSION-HANDOFF.md` "Direction change" for the full decision
record. This is sub-project 1 of 4 in that pivot. It touches only the backend
(`apps/api` + `workers/transcription`): no UI, no Tauri packaging, no new
functionality. The transcription algorithms themselves (probe, normalize,
inference, structure, quantize, assign, export) are untouched — this sub-project
changes only how the API talks to storage, the database, and the job runner.

Investigated directly against the current code before writing this spec:
- **DB models** (`aura_api/models.py`) use plain `String(36)` primary keys and
  SQLAlchemy's generic `JSON` column type throughout — no Postgres-specific types
  (no `UUID`, no `JSONB`, no `ARRAY`). The schema is already portable to SQLite
  with zero model changes.
- **Storage** is two separate boto3-only classes: `aura_api/storage.py` (presigned
  PUT/GET URLs, `head_object`) and `workers/transcription/src/aura_worker/storage.py`
  (`download_media_asset`/`put_bytes`/`get_bytes`/`put_file`/`get_file`). Both
  require an S3-compatible endpoint.
- **Upload flow** (`POST /v1/uploads`) hands the client a presigned S3 PUT URL —
  the client then PUTs bytes directly to the object store, bypassing the API
  process. This indirection exists to avoid proxying large media through a
  multi-tenant cloud API process; it has no purpose in a single local process
  talking to its own filesystem.
- **Job dispatch** (`aura_api/queue.py`) is the only Redis/RQ touchpoint in the
  whole codebase. `enqueue_transcription_job(job_id)` calls
  `transcription_queue.enqueue("aura_worker.runner.run_transcription_job", job_id, ...)`.
  Critically, `run_transcription_job(job_id: str) -> None` (in
  `aura_worker/runner.py`) already opens its own DB session and instantiates its
  own storage client internally — it takes only a `job_id` string and returns
  nothing. This is exactly the shape needed to submit to a thread pool with no
  signature changes.
- `infra/docker-compose.yml` runs Postgres, Redis, and MinIO for local dev/test.
  After this sub-project, none of them are needed to run the app or its tests.

## Goal

The API process runs as a single local Python process with zero external
services: no Postgres, no Redis, no S3/MinIO, no Docker required. Given a fixed
audio fixture, the app produces byte-identical MusicXML/MIDI output to today's
Postgres+S3+Redis stack, verified by running the existing end-to-end integration
test against the new stack.

## Non-Goals (deferred)

- **Tauri packaging / sidecar process management** — sub-project 2.
- **Any UI** — sub-project 3.
- **Semantic editing (edit-operation API, undo/redo, locks)** — sub-project 4.
  This sub-project does not touch `ScoreRevision`/edit endpoints beyond what's
  needed for storage/DB portability.
- **Multi-user / auth.** Still a single implicit `owner_id="anonymous"`, same as
  today. Not addressed here.
- **Concurrent job execution.** The thread pool is sized for one job at a time
  (see Architecture). Running multiple transcriptions concurrently is out of
  scope — matches the implicit single-job assumption the vertical slice already
  had (RQ's default worker also processed one job at a time locally).
- **Deleting `infra/docker-compose.yml`.** It becomes unnecessary for local
  dev/test but isn't removed by this sub-project — no user-facing reason to force
  that cleanup now.
- **Changing the transcription algorithms.** Probe/normalize/inference/structure/
  quantize/assign/export logic is untouched.

## Architecture

### Database: Postgres → SQLite

- `DATABASE_URL` becomes a `sqlite:///{path}` URL pointing at a file under a new
  `AURA_DATA_DIR` config value (defaults to a local `./data` directory for this
  sub-project — the real platform-appropriate app-data path is a sub-project 2
  packaging concern, not this one).
- `aura_api/db.py`'s `get_engine()` adds `connect_args={"check_same_thread": False}`
  when the URL scheme is `sqlite`, since the thread-pool job runner (below) opens
  its own session from a worker thread. `pool_pre_ping=True` stays for both
  backends.
- The existing single Alembic migration (`0001_initial.py`) is additive-only
  (`CREATE TABLE`, no `ALTER TABLE`), so it applies to SQLite unchanged — verified
  by reading it; no new migration needed.
- `psycopg2-binary` is dropped from both `apps/api/pyproject.toml` and
  `workers/transcription/pyproject.toml` — nothing else depends on it.

### Storage: S3 → local filesystem, consolidated into one client

`aura_worker` already imports directly from `aura_api` (`aura_api.db`,
`aura_api.models`), so the dependency direction is already worker→api. This
sub-project consolidates storage the same way: delete
`workers/transcription/src/aura_worker/storage.py` entirely and replace
`aura_api/storage.py`'s boto3 client with a single `LocalStorageClient` used by
both the API routers and the worker stages.

`LocalStorageClient` (in `aura_api/storage.py`), backed by
`{AURA_DATA_DIR}/blobs/{object_key}`:

- `write_bytes(key: str, data: bytes) -> None` — replaces `put_bytes`, creates
  parent directories as needed.
- `read_bytes(key: str) -> bytes` — replaces `get_bytes`.
- `write_file(key: str, local_path: Path) -> None` — replaces `put_file`.
- `read_file(key: str, dest_path: Path) -> Path` — replaces `get_file` and
  `download_media_asset` (same operation, one name).
- `head(key: str) -> dict | None` — replaces `head_object`; returns
  `{"ContentLength": n}` on an existing file, `None` if the file doesn't exist
  (`projects.py`'s 404 check on upload lookup stays unchanged).
- `presign_put`/`presign_get` are deleted — no longer meaningful with no
  separate storage HTTP endpoint (see Upload flow below; export download
  becomes a direct file response, not a redirect to a signed URL).

`aura_worker/stages/*.py` and `aura_worker/runner.py` update their
`WorkerStorageClient` references to `aura_api.storage.LocalStorageClient` (same
method names used at each call site change per the mapping above — this is a
mechanical rename, not a behavior change).

### Upload flow: presigned PUT → direct multipart upload

`POST /v1/uploads` changes from a two-step (create upload intent → client PUTs
to a presigned URL) to a single-step direct upload:

- Request becomes `multipart/form-data` with the file body, using FastAPI's
  `UploadFile` (needs `python-multipart` added as a new dependency to
  `apps/api/pyproject.toml` — not previously required since no endpoint parsed
  multipart bodies).
- `content_type` validation reads `UploadFile.content_type` instead of a
  separate JSON field; `_ALLOWED_CONTENT_TYPES` check is unchanged.
- The handler computes `object_key` (unchanged: `make_upload_object_key`),
  streams the upload body to `storage_client.write_file(object_key, ...)`, and
  returns `{"object_key": ...}` — no `upload_url` in the response.
- `CreateUploadRequest`/`CreateUploadResponse` in `schemas.py` update to match:
  drop `filename`/`content_type` as separate JSON fields (derived from the
  uploaded file instead) and drop `upload_url`.
- `GET /v1/exports/{id}` (`routers/exports.py`) stops calling
  `storage_client.presign_get` and instead returns the file directly (FastAPI
  `FileResponse` against the local blob path) — still a single GET, just no
  redirect-to-signed-URL indirection.

### Job dispatch: Redis/RQ → in-process thread pool

`aura_api/queue.py` replaces its Redis/RQ body with:

```python
from concurrent.futures import ThreadPoolExecutor
from aura_worker.runner import run_transcription_job

_executor = ThreadPoolExecutor(max_workers=1)

def enqueue_transcription_job(job_id: str) -> None:
    _executor.submit(run_transcription_job, job_id)
```

`run_transcription_job`'s signature and internals are untouched — it already
opens its own `Session` and storage client per call, which is exactly what a
thread-pool submission needs. The call site in `routers/jobs.py`
(`enqueue_transcription_job(job.id)`, called synchronously right after
`db.commit()`) is unchanged. `GET /v1/jobs/{id}` polling continues to work
unmodified since job progress is still written to the `transcription_jobs`
row in the (now SQLite) database, which the thread and the request-handling
thread both read/write through their own sessions.

`max_workers=1` matches the Non-Goals note above — one job at a time, matching
today's effective behavior with a single RQ worker.

### Config

`aura_api/config.py`'s `Settings` drops `redis_url`, `s3_endpoint_url`,
`s3_access_key`, `s3_secret_key`, `s3_bucket`, `s3_region`, adds
`data_dir: str = "./data"` (backs both the SQLite file path and the blob root).
`database_url` stays (now holds a `sqlite:///` URL instead of `postgresql+psycopg2://`).

### Dependency cleanup

Remove from both `apps/api/pyproject.toml` and `workers/transcription/pyproject.toml`:
`boto3`, `rq`, `redis`, `psycopg2-binary`. Add to `apps/api/pyproject.toml`:
`python-multipart` (required for `UploadFile` parsing, previously unneeded).

### Tests

Both `apps/api/tests/conftest.py` and `workers/transcription/tests/conftest.py`
currently default `DATABASE_URL` to a Postgres URL when unset. Both switch their
default to a SQLite URL (e.g. a temp-directory file, or `sqlite:///:memory:` if
a shared in-memory DB across the fixture's engine/session works cleanly —
verify empirically while implementing, since SQLite in-memory DBs are
connection-scoped and `check_same_thread=False` plus a single shared connection
may be needed; fall back to a temp file per test if memory mode causes
cross-session visibility issues). This is what actually proves the app runs
without `infra/docker-compose.yml` — if the test suite passes with no Postgres/
Redis/MinIO containers running, the offline goal is met.

## Testing

- Full existing `apps/api` and `workers/transcription` test suites pass against
  SQLite with no Docker containers running (the direct verification of "offline").
- The existing end-to-end integration test (guitar and piano fixtures) passes
  unchanged in assertions — same MusicXML/MIDI structural checks — proving the
  storage/DB/queue swap didn't change pipeline output.
- New test: `POST /v1/uploads` accepts a multipart file body and the returned
  `object_key` is immediately readable via `LocalStorageClient` (replaces the
  old presign-based upload test).
- New test: `GET /v1/exports/{id}` returns the file bytes directly (replaces
  the old presigned-URL-in-response test).
- New test: submitting two transcription jobs back-to-back both complete
  successfully through the thread pool (proves `max_workers=1` serializes
  correctly rather than dropping or corrupting the second job).
- Regression: `LocalStorageClient.head()` returns `None` for a nonexistent key,
  preserving `POST /v1/projects`'s existing 404-on-missing-upload behavior.

## Error Handling

No new `JobErrorCode` values — this sub-project doesn't touch job failure
semantics. A thread-pool `submit` that raises inside `run_transcription_job` is
already handled the same way today's RQ-invoked call is: the function's own
try/except around each stage catches stage failures and writes `error_code`/
`error_detail` to the job row (existing `runner.py` behavior, unchanged). An
exception escaping `run_transcription_job` itself (not caught internally) would
previously surface in RQ's job-failure tracking; under the thread pool it
becomes an exception stored on the `Future` that nothing currently retrieves —
acceptable for this sub-project since `run_transcription_job` already wraps its
own body in error handling that writes to the job row before any such escape
could happen, but worth a defensive `try/except Exception` around the
`_executor.submit` target (log + best-effort mark the job `error_code=INTERNAL_ERROR`
if the row is still `running`) so a truly unexpected exception doesn't vanish
silently into an unobserved `Future`.

## Definition of Done

- `apps/api` and `workers/transcription` test suites pass with
  `infra/docker-compose.yml` **not running** — no Postgres, Redis, or MinIO.
- A developer can run the API process standalone (`uvicorn` or equivalent),
  `POST` a fixture upload via multipart, create a project, start a
  transcription, poll it to completion, and download the MusicXML/MIDI export —
  all through one local Python process, no other services.
- Guitar and piano end-to-end fixture outputs are byte-for-byte/structurally
  identical (per existing e2e test assertions) to pre-migration output.
- `boto3`, `rq`, `redis`, `psycopg2-binary` no longer appear in either
  package's dependencies; `python-multipart` is added where needed.
- `docs/superpowers/SESSION-HANDOFF.md` updated to record this sub-project done
  and point at sub-project 2 (Tauri shell + packaging) as next.
