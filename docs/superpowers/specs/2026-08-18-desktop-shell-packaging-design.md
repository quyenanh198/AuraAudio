# Desktop Shell + Packaging — Design

Offline desktop app sub-project 2. Depends on sub-project 1 (offline
backend adaptation, done). See `docs/superpowers/SESSION-HANDOFF.md`
"Direction change" for how this supersedes `ARCHITECTURE.md`'s client model.

## Context

Sub-project 1 turned the backend into something that runs as a single
local process with no external services: SQLite instead of Postgres, a
filesystem `LocalStorageClient` instead of S3, and an in-process thread
pool instead of Redis/RQ. What it did *not* do is give that process a way
to be launched, supervised, or shut down by anything other than a
developer typing `uvicorn` in a terminal with `.envrc` sourced.

This sub-project builds that missing layer: a Tauri desktop application
that owns the lifecycle of the Python backend as a managed sidecar, plus
the packaging story that turns the workspace into something installable.
Per the approved decomposition there is **no real UI here** — the window
shows enough to prove the backend is alive and reachable, and nothing more.
Score rendering and playback are sub-project 3.

### What was verified before writing this spec

Every claim below was checked directly against the code or by running it,
per this project's "verify before you write into the plan" rule. Three of
these findings changed the design, so they are recorded with their evidence
rather than asserted:

1. **A fresh install cannot create a project — this is a live bug, not a
   packaging concern.** Nothing runs Alembic at startup. `main.py` has no
   migration hook; only `apps/api/tests/conftest.py` creates the schema,
   via `Base.metadata.create_all(engine)`. Reproduced end to end: with a
   fresh `AURA_DATA_DIR` and a clean SQLite path, `uvicorn` starts,
   `/healthz` returns ok, `POST /v1/uploads` returns 201 (it only touches
   the filesystem), and then `POST /v1/projects` returns **HTTP 500** with
   `sqlite3.OperationalError: no such table: projects`. Sub-project 1's
   manual smoke test did not catch this because it ran against a data
   directory whose tables already existed from earlier runs. The desktop
   app makes this unmissable: every first launch on every user's machine
   is exactly this fresh-install case.

2. **Configuration is read at import time, so it must be in the
   environment before the interpreter starts.** `aura_api/config.py` runs
   `settings = Settings()` at module scope and `aura_api/db.py` runs
   `engine = get_engine()` at module scope, reading `os.environ
   ["DATABASE_URL"]` with no default and raising `KeyError` if it is
   absent. The shell therefore cannot configure the backend after spawn,
   and cannot rely on a `.env` file that a packaged app has no natural
   place to put. It must pass environment variables into the child process
   at spawn time.

3. **`ffmpeg` and `ffprobe` are invoked as bare command names** —
   `subprocess.run(["ffmpeg", ...])` in `stages/normalize.py` and
   `["ffprobe", ...]` in `ffmpeg_utils.py` — so they resolve through
   `PATH`. A packaged app cannot assume either exists on a user's machine.
   This is not hypothetical: the container this spec was written in had
   neither, and both e2e tests failed with `FileNotFoundError: [Errno 2]
   No such file or directory: 'ffprobe'` until ffmpeg was installed.

Two further facts that shape the design but did not surprise:

4. **The job queue is in-process and holds no durable state.**
   `aura_api/queue.py` is a `ThreadPoolExecutor(max_workers=1)`. Jobs move
   `pending` → `running` → `succeeded`/`failed` in `runner.py`. Killing
   the sidecar mid-job leaves that job's row stuck at `running` forever,
   with no process anywhere intending to finish it. On a server that is
   rare; on a desktop app it is routine, because users close windows.

5. **There is no CORS middleware and no authentication of any kind.**
   Every route is open to anything that can reach the port.

## Goal

A Tauri desktop application that a user can launch, which starts the
Python backend automatically, waits until it is genuinely ready, shows a
native window pointed at it, and shuts it down cleanly on quit — with the
backend's first-launch, filesystem, and binary-dependency assumptions
fixed so that this works on a machine that has never run the project
before.

## Non-Goals (deferred)

- **Any real UI.** The window renders a minimal status page. Upload
  controls, score rendering, and playback are sub-project 3.
- **Code signing, notarization, auto-update, installers per platform.**
  This sub-project produces a bundle that runs; distribution is later.
- **The ONNX backend swap.** Proven viable and ready to apply (see
  "Bundle size" below), but deliberately out of scope so the shell is not
  blocked behind a dependency migration.
- **Multi-platform CI.** The plan targets one development platform; the
  per-platform build matrix comes with distribution.
- **Editing.** Sub-project 4.

## Architecture

### Decision 1: sidecar packaging — relocatable interpreter, not PyInstaller

**Chosen: ship a `python-build-standalone` interpreter plus the installed
site-packages as Tauri resources, and spawn it directly.**

The dependency tree decides this. The backend pulls in TensorFlow (via
basic-pitch), numba and llvmlite, librosa, resampy, scipy, scikit-learn
and music21. That set is close to a worst case for a freezer:

- `music21` ships a large data corpus it locates relative to its package
  directory, and `basic_pitch` ships `saved_models/icassp_2022/` (four
  model formats) the same way. Both are data files a freezer must be told
  about explicitly, and both fail at *runtime* rather than build time when
  they are missed.
- `numba`/`llvmlite` compile at runtime and are a known source of
  PyInstaller breakage.
- `resampy` imports `pkg_resources` at runtime, which is why
  `setuptools<81` is already pinned in `workers/transcription`. Frozen
  bundles and `pkg_resources` interact badly.

A relocatable interpreter sidesteps every one of these, because the
on-disk layout the libraries expect is exactly the layout they get. The
cost is honest: a larger bundle and a per-platform build step to fetch the
right interpreter and install wheels for that platform.

**Fallback: PyInstaller one-dir**, with explicit trigger conditions — adopt
it only if bundle size becomes the binding constraint *after* the ONNX swap
lands, or if a platform target appears where python-build-standalone has no
usable interpreter. Recording the trigger matters more than recording the
alternative: without it, a future session re-litigates this from scratch.

### Decision 2: local security — loopback binding plus a per-launch token

**Chosen: bind `127.0.0.1` only, and require a per-launch shared secret on
every request.**

Loopback binding alone is not sufficient here, and it is worth being
precise about why rather than invoking "defense in depth":

- Every other process on the machine — including anything the user ran
  that is not privileged — can reach the port. This API accepts arbitrary
  audio uploads, writes files under the data directory, and serves export
  downloads back. That is a real read/write surface over a user's files.
- A **web page in the user's browser** can send requests to
  `http://127.0.0.1:<port>` too. CORS does not prevent the request being
  *sent*; it prevents the page from *reading the response*. For endpoints
  with side effects — upload, project creation, job start — an unreadable
  response is no protection at all. DNS rebinding can additionally make a
  page same-origin with the sidecar.

The mitigation is cheap: the shell generates a random token per launch,
passes it to the sidecar in the environment (alongside the other config it
already must pass), and sends it as a header on every request. The API
gains one small middleware that rejects requests without it. `/healthz`
stays unauthenticated so the readiness handshake needs no secret ordering.
Validating the `Host` header against `127.0.0.1[:port]` closes the DNS
rebinding case in the same middleware.

The token lives only in the shell's memory and the child's environment —
never on disk, never in a config file, and it changes every launch.

### Sidecar lifecycle

**Port.** The shell must not hardcode a port; a fixed port collides with
whatever else the user runs and makes two instances mutually exclusive. The
shell binds an ephemeral port itself, closes it, and passes the number to
uvicorn via `--port` — then it knows the port without parsing child output.
(Parsing uvicorn's stdout is the alternative; it is more fragile because
the banner format is not a stable contract.)

**Readiness.** The window must not load until the backend answers. Startup
here is not fast — importing TensorFlow alone is seconds — so the shell
polls `GET /healthz` until it returns ok, with a timeout that surfaces a
real error rather than a blank window. `/healthz` already exists and
touches nothing, which makes it a correct readiness probe: it will answer
before migrations finish, so the shell must poll a readiness signal that
covers migrations too (see below), not just process liveness.

**Migrations on startup.** Given finding 1, something must run `alembic
upgrade head` before the first request. This belongs in the backend, not
the shell: the backend owns its schema, the shell should not need to know
Alembic exists, and putting it in the backend fixes the bug for
`uvicorn`-in-a-terminal too. A FastAPI lifespan startup hook is the right
seam. Because migrations then run *during* startup, `/healthz` answering
is no longer sufficient evidence of readiness — the design adds a distinct
ready signal that flips only after migrations complete.

**Shutdown.** On window close the shell terminates the sidecar, escalating
to a hard kill after a grace period, and must not leave orphans if the
shell itself dies. Tauri's sidecar handling covers the normal path; the
crash path needs explicit attention.

**Stale jobs.** Per finding 4, a job left `running` by a killed sidecar is
never resumed. On startup the backend marks any job still in `running` as
`failed` with a clear reason. Marking them failed rather than silently
re-queuing them is deliberate: re-running inference automatically on every
launch could burn minutes of CPU without the user asking.

### Data directory

`data_dir` currently defaults to `./data`, resolved against the process's
working directory — for a packaged app that is wherever the OS happened to
start it, quite possibly read-only. The shell resolves the platform's
per-user app-data directory (Tauri exposes this) and passes it as
`AURA_DATA_DIR`, with `DATABASE_URL` pointing at a SQLite file inside it.
The backend already creates the directory tree it needs, so no backend
change is required beyond accepting the values it is given.

### `ffmpeg` bundling

Both binaries ship as Tauri resources, and the worker learns to prefer an
explicit path over bare `PATH` lookup — an env var the shell sets, falling
back to today's behaviour when unset so developer workflows and the test
suite are unaffected.

### Bundle size

The current workspace venv is **2.3 GB**, of which **TensorFlow is 1.4 GB**.
That is the dominant cost of the bundle and it is worth stating plainly
that no packaging choice here will hide it.

It is also solvable, and the solution was proven while scoping this
sub-project rather than assumed: `basic_pitch` ships its model in four
formats and selects a backend by import-time priority (TF → CoreML →
TFLite → ONNX), and our call site uses that auto-selected constant. With
TensorFlow absent and `onnxruntime` present, inference produced
**bit-identical output** to the TensorFlow baseline on both the diatonic
melody and guitar pluck fixtures — every pitch, onset, offset and
amplitude equal — with no worker code change. The full runtime dependency
set measured **749 MB** instead of 2.3 GB, and the `numpy<2` pin (a
TensorFlow 2.14 ABI artifact) could be dropped with output still identical
under numpy 2.4.6.

Two caveats keep this out of scope here rather than folded in: basic-pitch
declares TensorFlow as a hard requirement on Linux/Windows for Python
≥3.11, so removing it needs a dependency override; and macOS selects
CoreML rather than ONNX, so the backend choice is genuinely per-platform.
Both are tractable, neither is this sub-project's problem.

## Testing

The parts of this sub-project that can be verified without a display are
the parts most likely to be wrong, and they should be tested as such:

- **Backend, in the existing pytest suite:** migrations run on startup
  against a fresh database and the first project creation succeeds (a
  direct regression test for finding 1); stale `running` jobs are failed
  on startup; the token middleware rejects a missing or wrong token,
  accepts a correct one, and leaves `/healthz` open; the `Host` check
  rejects a foreign host header.
- **Shell, in Rust tests:** port selection returns a usable free port;
  environment assembly puts every required variable in the child's
  environment; readiness polling times out rather than hanging.
- **Manual, on a real desktop:** launch, window appears, backend reachable,
  quit leaves no orphaned process. This is the part this container cannot
  verify, and the plan says so rather than pretending otherwise.

## Error Handling

Failures here are user-visible in a way server failures are not — there is
no log the user will read. The backend failing to start, the readiness
timeout expiring, and the port being unavailable must each produce a
window with a real message, not a blank webview or a silent exit.

## Definition of Done

1. `alembic upgrade head` runs automatically at backend startup; a fresh
   data directory yields a working app with no manual step, covered by a
   test that fails against today's code.
2. Jobs stuck in `running` from a previous launch are failed at startup.
3. The API requires a per-launch token on all routes except `/healthz`,
   and validates the `Host` header.
4. The worker resolves `ffmpeg`/`ffprobe` from an explicit path when told
   to, falling back to `PATH`.
5. A Tauri shell spawns the backend with a per-user data directory, an
   ephemeral port, and a generated token; waits for genuine readiness;
   shows a window; and shuts the sidecar down on quit without orphans.
6. The full existing suite still passes, plus the new backend tests.
7. Sub-project 1's offline guarantee is preserved: no network, no external
   services.
