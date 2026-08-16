# Desktop Shell + Packaging — Design

## Context

Sub-project 1 (offline backend adaptation, merged to `main`) made AuraAudio's
backend run as a single local Python process with zero external services —
SQLite, `LocalStorageClient` (local filesystem), and an in-process thread
pool replaced Postgres/S3/Redis. This sub-project is #2 of 4 in the
offline-desktop-app pivot (`docs/superpowers/SESSION-HANDOFF.md`, "Direction
change"): wrap that backend in a Tauri desktop shell. No UI work happens
here — sub-project 3 owns the actual product surface. This sub-project's
entire job is proving the packaging and process-lifecycle model: a Tauri
app that spawns the Python backend as a managed sidecar process and shows a
native window pointed at it.

Investigated directly before writing this spec: this repo has zero
Rust/Tauri/PyInstaller artifacts today — `apps/` contains only `api/`.
`apps/api`'s current run command (`apps/api/Dockerfile:13`) is
`uv run --package aura-api uvicorn aura_api.main:app --host 0.0.0.0 --port 8000`
— that assumes a `uv`/Python environment on the host, which an end user's
machine won't have. The sandbox this plan is being written in has `cargo`
1.94.1, `rustc` 1.94.1, and Node/npm available, but not PyInstaller (installable).
This means the Linux half of packaging can be genuinely built and verified
here; macOS/Windows bundling cannot (see Non-Goals).

## Goal

A Tauri desktop app that, when launched, starts the bundled Python backend
as a child process, waits for it to be ready, opens a native window loading
a placeholder page served by that backend, and cleanly terminates the
backend when the app quits. No `uv`, Python, or any other runtime needs to
be pre-installed on the target machine — the backend ships as a
self-contained bundle inside the Tauri app package. This is verified for
real on Linux in this repo's own CI-equivalent (this sandbox); macOS and
Windows builds are configured but not build-verified here.

## Non-Goals (deferred)

- **Any real UI.** The webview loads a static placeholder (see
  Architecture) — no score preview, no upload flow. Sub-project 3's job.
- **macOS/Windows build verification.** This sandbox is Linux-only. Tauri
  config targets all three platforms and the plan includes the steps a
  developer would run on those platforms, but nothing here claims those
  builds were actually exercised in this repo's history — only Linux was.
- **Code signing / notarization / auto-update.** Real distribution concerns,
  out of scope for a process-lifecycle proof.
- **Dynamic port allocation.** The backend binds a fixed, hardcoded port
  for this sub-project. A real port-conflict-avoidance scheme (bind
  port 0, have the child report back which port it took) is a reasonable
  future refinement, not required to prove the packaging model works.
- **Multi-window / system tray / native menus.** Single window, no chrome
  beyond what Tauri gives by default.
- **CI/build-pipeline automation for the desktop app.** This sub-project
  proves a developer can build it locally; automating that build is
  separate, unscoped work.

## Architecture

### Backend bundling: PyInstaller `--onedir`, shipped as a Tauri resource

Two PyInstaller modes exist: `--onefile` (single executable, unpacks to a
temp directory on every launch) and `--onedir` (a folder containing the
executable plus its dependencies, no per-launch unpacking). `--onefile` is
what Tauri's `externalBin`/sidecar mechanism most naturally expects (a
single named binary per target triple) — but `apps/api`'s real dependency
tree includes tensorflow and basic-pitch (used by `workers/transcription`
for real ML inference, and now imported in-process by the API per
sub-project 1's thread-pool dispatch). Unpacking that dependency tree from
a compressed onefile archive on every app launch is a real, avoidable
startup-latency cost for a desktop app. `--onedir` avoids it.

Decision: PyInstaller `--onedir`, and instead of Tauri's `externalBin`
sidecar convention (which assumes one file), bundle the whole `--onedir`
output as a Tauri **resource** directory (Tauri's `bundle.resources` config
copies arbitrary files/folders into the packaged app, addressable at
runtime via its resource-resolution API) and spawn the entry executable
inside that resource directory directly via Tauri's `Command` API, rather
than through the `externalBin` convention. This is a documented, understood
tradeoff, not a default — **Task 1 of the implementation plan is a real
spike verifying PyInstaller can actually bundle `apps/api` successfully at
all** (tensorflow bundling is known to be occasionally fragile — hidden
imports, large binary size) before the rest of the plan's architecture is
treated as locked. If the spike finds `--onedir` doesn't work cleanly, the
plan's later tasks get revisited, not silently forced through.

### Process lifecycle

1. Tauri app starts. Rust `setup` hook spawns the bundled backend
   executable (from the resource directory) as a child process, with
   `AURA_DATA_DIR` and `DATABASE_URL` env vars set per the "App-data path"
   section below, and a fixed port (e.g. `8317` — chosen to avoid common
   dev-server collisions, not load-bearing, easy to change) passed via
   `--port` or an env var the backend already supports (`uvicorn` takes
   `--port` on its own CLI; the bundled entrypoint should accept the same).
2. Before creating the webview window, poll the backend's root/health
   endpoint (`GET /` or similar — check what `apps/api/src/aura_api/main.py`
   actually exposes unauthenticated at the app root before assuming a path)
   with a short interval and a bounded timeout (a few seconds is not
   enough — tensorflow import alone takes real wall-clock time; verified
   empirically during sub-project 1's e2e tests, several seconds per
   process start). On timeout, show an error state rather than a blank/
   broken window.
3. Once healthy, create the window pointed at
   `http://127.0.0.1:<port>/<placeholder-path>`.
4. On Tauri app exit (window-close / process-exit event), send the child
   process a termination signal and wait briefly for it to exit before the
   Tauri process itself exits — don't just let it become an orphan. This
   also closes the loop on a sub-project-1 final-review Minor finding
   (`ThreadPoolExecutor` never explicitly shut down) — OS-level process
   termination handles it regardless of whether the Python side adds its
   own graceful-shutdown hook, but the spike/task work should confirm no
   zombie process survives a normal quit.

### App-data path

Sub-project 1's `.envrc` used a placeholder `AURA_DATA_DIR=./data`,
explicitly deferring "the real platform-appropriate app-data path" to this
sub-project. Tauri's `app_data_dir()` API resolves the correct per-OS
location (`~/Library/Application Support/<bundle-id>/` on macOS,
`%APPDATA%/<bundle-id>/` on Windows, `~/.local/share/<bundle-id>/` on
Linux, following each platform's convention — verify exact values against
the real API when implementing rather than assuming from memory). The Rust
setup hook resolves this path, ensures it exists, and passes it to the
child process as `AURA_DATA_DIR`; `DATABASE_URL` is derived from it
(`sqlite:///<data-dir>/aura.db`) the same way `.envrc` did, just computed
at runtime instead of hardcoded.

### Placeholder page

A single static `dist/index.html` (no JS framework — that choice belongs
to sub-project 3) that fetches the backend's health endpoint and displays
something simple confirming the pipe works end-to-end (e.g. "Backend
running: `<status>`"). This is the only content the webview shows.

### Repository layout

New top-level package following the existing `apps/api` /
`workers/transcription` convention:

```text
apps/desktop/
  src-tauri/
    Cargo.toml
    tauri.conf.json
    src/main.rs
    resources/           # PyInstaller --onedir output lands here at build time
  dist/
    index.html            # static placeholder
  build-backend.sh          # or equivalent — runs PyInstaller, stages output
                            # into src-tauri/resources/ before `tauri build`
```

## Testing

Given this is infrastructure/packaging work rather than application logic,
"testing" here means real, executed verification steps rather than a unit
test suite:

- PyInstaller bundle actually runs standalone (no `uv`/Python on `PATH`
  needed) and serves the health endpoint when launched directly, before
  any Tauri integration is attempted.
- `cargo build`/`tauri dev` actually launches the window and shows the
  placeholder page content, with the bundled backend (not a
  developer's local `uv run` process) serving it.
- Killing the Tauri process (simulated crash, not just clean quit) is
  checked for orphaned child processes — a real, reproducible check
  (e.g. `ps` before/after), not assumed from Tauri's documentation alone.
- `tauri build` produces a real Linux package (whatever format Tauri
  targets by default on Linux — AppImage/deb, confirm during
  implementation) that launches on a machine with no `uv`, `python`, or
  the repo's own `.venv` on `PATH`.
- macOS/Windows: the plan documents the equivalent commands a developer
  would run, but this repo has no automated proof they succeed (Non-Goal).

## Error Handling

- Backend fails to start (crash, port already bound, missing resource
  files): the health-check polling in the Rust lifecycle code times out
  and the app shows an explicit error state, not a blank/hung window.
- Backend process dies mid-session (e.g. an unhandled crash after
  startup): out of scope to auto-restart it for this sub-project — a
  future refinement. The window will simply stop getting responses;
  acceptable for a no-real-UI proof-of-concept.

## Definition of Done

- `apps/desktop/` exists with a working Tauri project.
- PyInstaller successfully bundles `apps/api` (with its full real
  dependency tree, including tensorflow/basic-pitch) into a standalone
  `--onedir` output, verified by launching it directly (no `uv`/Python
  environment active) and hitting its health endpoint.
- `tauri dev` (or the built app) launches a native window, spawns the
  bundled backend as a child process, waits for it to be healthy, and
  displays the placeholder page's live status against the real backend.
- Quitting the app terminates the child process — verified by process
  inspection, not assumed.
- `AURA_DATA_DIR`/`DATABASE_URL` resolve to a real per-OS app-data path via
  Tauri's API, not the sub-project-1 placeholder.
- `tauri build` produces a working Linux package, verified by running it
  on a machine/container with no Python toolchain present.
- `docs/superpowers/SESSION-HANDOFF.md` updated to record this done and
  point at sub-project 3 (score preview + playback UI) next.
