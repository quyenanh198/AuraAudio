# Desktop Shell + Packaging Implementation Plan

Spec: `docs/superpowers/specs/2026-08-18-desktop-shell-packaging-design.md`.

## How to read this plan

Tasks 1–4 are backend work: fully verifiable in the existing pytest suite,
on any machine, with no desktop involved. Tasks 5–8 are shell work that
needs a Rust toolchain and a real display.

**The Rust in tasks 5–8 has not been compiled.** It was written against
Tauri's documented sidecar API, but the session that produced this plan had
no Rust toolchain and no display, so it carries none of the "verified
directly against the real library" weight that the Python code in tasks 1–4
does. Treat those snippets as a design sketch to be checked against the
Tauri version you actually pin, not as complete code in the sense this
project's planning rules normally require. This is called out here because
this repo's own history has two worked examples of library behaviour that
was assumed, written into a plan, and turned out wrong — see
`SESSION-HANDOFF.md`.

Do tasks 1–4 first regardless. They fix a live bug and stand on their own
even if the shell work is deferred.

## Execution status

**Tasks 1–4: DONE (2026-08-18)**, commits `1641257`, `e67cdb8`, `c76ee33`,
`63dc5bc`. Suite 157/157; ruff unchanged at its 96-error baseline. Each
task's tests were mutation-checked rather than merely observed passing —
see each commit message for what was broken and which tests caught it.

All four were then verified together against a real `uvicorn` process with
a fresh data directory, a token set, and explicit binary paths — i.e. the
configuration the shell will actually produce: readiness poll open without
a token (200), API rejected without one (401) and with a wrong one (401),
foreign `Host` rejected (400), upload and project creation accepted (201),
and a full transcription driven to `succeeded`. Interrupted-job recovery
was verified by planting a `running` row and restarting: it came back
`failed` with the interruption reason, and an already-succeeded job in the
same database was left untouched.

Two things surfaced during execution that were not in the plan as written:

- **The Alembic move needed `parents[2]`, not `parents[1]`**, in
  `env.py`'s `sys.path` climb, and `alembic.ini`'s `script_location` is
  relative and had to change too — re-check the CLI from `apps/api` after
  the move.
- **The test conftest bootstrapped the schema with
  `Base.metadata.create_all`, which conflicts with the new startup
  migration.** `create_all` leaves no `alembic_version` row, so Alembic
  then tried to create tables that already existed and failed with "table
  projects already exists". This was invisible until a test entered a
  `TestClient` as a context manager — that is what runs the lifespan
  handler; the existing tests use `TestClient(app)` without `with` and so
  never ran startup at all. The conftest now bootstraps via
  `run_migrations()`, so tests exercise the same path production does.
  Worth knowing for task 6: any future test that needs startup behaviour
  must use the context-manager form.

**Tasks 5–8: not started.** They need a Rust toolchain and a display.

## Global Constraints

- Every task ends with the full suite green: `source .envrc && make test`.
- A fresh container needs `uv sync --all-packages --all-extras`, a
  recreated `.envrc`, and `ffmpeg` installed — see `SESSION-HANDOFF.md`
  "Quick start".
- `make lint` reports 96 pre-existing ruff errors. Compare counts before
  and after; do not expect zero.
- Development is on the branch `claude/resume-session-afs2az`, not `main`.

---

## Task 1: Run migrations at startup

Fixes the live bug in the spec's finding 1: a fresh install 500s on the
first database write with `no such table: projects`.

### 1a. Relocate the Alembic directory into the package

`apps/api/pyproject.toml` builds the wheel from `packages =
["src/aura_api"]`, so `apps/api/alembic/` is **not** in the wheel today and
would not ship with a packaged app. Move it:

```
git mv apps/api/alembic apps/api/src/aura_api/alembic
```

Then update `apps/api/alembic.ini`'s `script_location` to
`src/aura_api/alembic`, and fix the `sys.path` line in `env.py`, which
climbs to `parents[1]` from the old location and needs `parents[2]` from
the new one:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
```

Confirm the CLI still works from `apps/api`: `alembic upgrade head`.

### 1b. Write the failing test first

`apps/api/tests/test_startup_migrations.py`:

```python
import sqlite3
from pathlib import Path

from aura_api.migrations import run_migrations


def test_run_migrations_creates_schema_in_an_empty_database(tmp_path: Path, monkeypatch):
    db = tmp_path / "fresh.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    run_migrations()

    tables = {
        row[0]
        for row in sqlite3.connect(db).execute(
            "select name from sqlite_master where type='table'"
        )
    }
    assert "projects" in tables
    assert "transcription_jobs" in tables


def test_run_migrations_is_idempotent(tmp_path: Path, monkeypatch):
    db = tmp_path / "twice.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")

    run_migrations()
    run_migrations()  # must not raise on an already-migrated database
```

### 1c. Implement

New file `apps/api/src/aura_api/migrations.py`. The absolute
`script_location` is the point — a packaged app's working directory is
whatever the OS gave it, so Alembic's default relative resolution cannot
be relied on. This exact approach was verified working from `/` as the
working directory, and verified idempotent on a second run:

```python
from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def run_migrations() -> None:
    """Bring the database up to head.

    Called at API startup so a fresh install works with no manual step.
    script_location must be absolute: a packaged app's working directory
    is not the repo, so Alembic's relative default does not resolve.
    """
    url = os.environ["DATABASE_URL"]
    if url.startswith("sqlite:///"):
        db_file = url[len("sqlite:///") :]
        if db_file != ":memory:":
            Path(db_file).parent.mkdir(parents=True, exist_ok=True)

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "head")
```

### 1d. Wire it into startup

In `apps/api/src/aura_api/main.py`, add a lifespan handler. Note that
`aura_api.db` builds its engine at import time, so `run_migrations` must
open its own connection rather than reuse that engine — the code above
does.

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from aura_api.migrations import run_migrations


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="AuraAudio API", lifespan=lifespan)
    ...
```

### 1e. Prove it against the real server

The unit tests above cover the function. Reproduce the *original* bug
report to prove the wiring, since a passing unit test would not have
caught it:

```bash
rm -rf /tmp/fresh && mkdir -p /tmp/fresh
DATABASE_URL="sqlite:///tmp/fresh/aura.db" AURA_DATA_DIR=/tmp/fresh \
  uv run --package aura-api uvicorn aura_api.main:app --port 8899 &
curl -s -X POST http://127.0.0.1:8899/v1/uploads \
  -F "file=@<some>.wav;type=audio/wav"          # 201, returns object_key
curl -s -X POST http://127.0.0.1:8899/v1/projects \
  -H 'Content-Type: application/json' \
  -d '{"title":"t","instrument":"guitar","object_key":"<key>"}'
```

Before this task that second call returns **500** with `no such table:
projects`. After it, it must return a created project. Run it both ways —
stash the change and watch it fail — rather than trusting the after state.

---

## Task 2: Fail stale `running` jobs at startup

Per spec finding 4, a sidecar killed mid-job leaves a row at `running`
that nothing will ever finish. Desktop users close windows routinely.

Test in `apps/api/tests/test_startup_migrations.py` (or a sibling): insert
a job at `running`, call the recovery function, assert it lands at
`failed` with a reason naming the interruption, and assert jobs at
`pending`, `succeeded` and `failed` are untouched. That last assertion is
the one that matters — a recovery that also clobbers `pending` jobs would
silently discard queued work, and a test that only checks the `running`
row would not notice.

Implement as `recover_interrupted_jobs()` alongside `run_migrations()`,
called from the same lifespan handler immediately after it. Use the
existing `JobErrorCode` vocabulary rather than inventing a new string;
check `score_schema.models.JobErrorCode` for the closest existing member
and add one only if none fits.

Deliberately **not** re-queuing these jobs: re-running inference costs
minutes of CPU, and doing that unbidden on every launch is worse than
showing the user a failed job they can retry.

---

## Task 3: Per-launch token and Host validation

Spec decision 2. One middleware, in `apps/api/src/aura_api/main.py` or a
new `auth.py`.

Behaviour:
- If `AURA_API_TOKEN` is **unset**, the middleware is inert. This keeps
  every existing test, and the developer `uvicorn` workflow, working
  unchanged — the shell is what sets it.
- If set, every request must carry it in a header (`X-Aura-Token`);
  otherwise 401.
- `/healthz` is always exempt, so the shell's readiness poll needs no
  secret ordering.
- When set, reject requests whose `Host` header is not `127.0.0.1` or
  `127.0.0.1:<port>` — this is the DNS-rebinding case from the spec, and
  it is why the check lives next to the token rather than in the shell.

Tests: unset token → existing behaviour; set + correct header → 200; set +
missing header → 401; set + wrong token → 401; `/healthz` reachable in all
cases; foreign `Host` → rejected. Use a constant-time comparison
(`hmac.compare_digest`) rather than `==`.

---

## Task 4: Explicit `ffmpeg`/`ffprobe` paths

Spec finding 3. Both are called as bare names in
`workers/transcription/src/aura_worker/stages/normalize.py` and
`ffmpeg_utils.py`.

Add a small resolver — `AURA_FFMPEG_PATH` / `AURA_FFPROBE_PATH` env vars
if set, else today's bare name — and route both call sites through it.
Falling back to the bare name is what keeps the test suite and developer
workflow untouched.

Test the resolver directly (set/unset), and assert the call sites use it;
a test that only covers the resolver would pass even if a call site still
hardcodes `"ffmpeg"`, which is the mistake to avoid here.

---

## Task 5: Tauri scaffold

Scaffold under `apps/desktop/`. Pin the Tauri version in the plan record
once chosen, since the sidecar API differs across major versions and the
sketches below assume v2.

Deliverables: a window that opens, a bundled minimal status page, and no
backend yet. Getting a blank window to appear is a real checkpoint — do
not fold it into the sidecar work.

---

## Task 6: Sidecar spawn, port, environment, readiness

The heart of the shell, and the part with the most unverified assumptions.

**Port.** Bind an ephemeral port, read the assigned number, drop the
listener, pass it to uvicorn with `--port`. There is a small race between
releasing and re-binding; it is accepted here because the alternative —
parsing uvicorn's stdout banner — depends on a format that is not a stable
contract. If the race bites in practice, revisit rather than pre-optimize.

**Environment.** Per spec finding 2 the child must receive, at spawn time:
`DATABASE_URL`, `AURA_DATA_DIR`, `AURA_API_TOKEN`, and the two ffmpeg path
variables from task 4. `DATABASE_URL` has no default and raises `KeyError`
at import if missing, so a missing variable shows up as an immediate child
exit — make that path produce a real error dialog.

**Data directory.** Resolve the platform per-user app-data directory
through Tauri's path API; do not construct these paths by hand.

**Readiness.** Poll `GET /healthz` until ok, with a timeout. Note the
ordering problem the spec raises: with task 1 in place migrations run
during startup, so `/healthz` answering does not by itself prove the
schema exists. Either poll an endpoint that touches the database, or add a
distinct ready flag that flips after the lifespan handler completes. Decide
this at execution time against the real startup sequence; the second option
is cleaner but needs the flag to exist.

Rust-side unit tests worth having: port helper returns a bindable port;
environment assembly contains every required key; readiness polling
returns an error on timeout rather than blocking forever.

---

## Task 7: Shutdown and orphan handling

Terminate the sidecar on window close, escalate to a hard kill after a
grace period, and handle the case where the shell dies without running its
own cleanup. Verify by launching, quitting, and confirming no Python
process survives — then again with the shell killed uncleanly, which is
the case that actually leaks.

---

## Task 8: Manual verification on a real desktop

Not scriptable and not skippable: launch the packaged app on a machine
that has never run it, confirm the window appears, confirm the backend is
reachable from it, drive one transcription end to end, quit, and confirm no
orphaned process. A fresh machine matters — it is the case task 1 exists
for.

---

## Definition of Done

Mirrors the spec's. Tasks 1–4 are independently shippable and should be
committed as they land rather than held for the shell.
