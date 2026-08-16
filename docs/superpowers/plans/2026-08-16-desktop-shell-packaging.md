# Desktop Shell + Packaging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Tauri desktop app that spawns AuraAudio's Python backend as a managed sidecar process, opens a native window loading a placeholder page once the backend is healthy, and cleanly terminates the backend on quit — no `uv`/Python needed on the target machine.

**Architecture:** PyInstaller `--onedir` bundles `apps/api` (full dependency tree, including tensorflow/basic-pitch) into a standalone executable. That bundle is shipped as a Tauri `resources` directory (not the `externalBin` single-file sidecar convention — `--onedir` avoids per-launch unpacking of a heavy ML dependency tree). A Rust `setup` hook spawns the bundled executable, polls `GET /healthz` until ready, then opens the window at a static placeholder page. `AURA_DATA_DIR`/`DATABASE_URL` are computed at runtime from Tauri's real per-OS app-data path (replacing sub-project 1's `./data` placeholder).

**Tech Stack:** Rust + Cargo (Tauri, confirmed present: cargo 1.94.1), PyInstaller (not yet installed — Task 1 installs it), Node/npm present but likely unneeded (static HTML placeholder, no frontend framework).

**Spec:** `docs/superpowers/specs/2026-08-16-desktop-shell-packaging-design.md`

## Global Constraints

- **This sandbox is Linux-only.** Every task's verification steps run and must actually pass here. Nothing in this plan claims macOS/Windows were build-verified — tasks that produce cross-platform config note the untested platforms explicitly rather than implying they were checked.
- **No exact Rust/Tauri API signatures are prescribed in this plan.** Unlike the Python work in sub-project 1 (verified against real, already-known code), Tauri's exact v2 API surface (Command/process-spawning API names, resource-resolution API names, app-data-dir API names) is genuinely uncertain territory here. Every Rust-writing task requires the implementer to (a) confirm the installed Tauri CLI's actual version, (b) scaffold via the official `create-tauri-app`/`cargo tauri init` generator rather than hand-writing `Cargo.toml`/`tauri.conf.json` from scratch, and (c) consult that generated boilerplate plus the Tauri version's own bundled docs/examples (not memorized API names) before writing spawn/health-check/shutdown logic. Report the actual API names used in each task's report so later tasks and review can verify them, not just trust they're plausible-sounding.
- **`GET /healthz` is the real, already-existing health endpoint** (`apps/api/src/aura_api/main.py`, returns `{"status": "ok"}`, no auth, no dependencies) — use this exact path, don't invent a different one.
- **Fixed port for this sub-project** (dynamic allocation is an explicit Non-Goal in the spec) — pick one unlikely to collide with common dev servers (e.g. `8317`) and use it consistently across every task; don't let different tasks pick different ports.
- **`apps/desktop/` is the new package root**, following the existing `apps/api` / `workers/transcription` convention.
- **PyInstaller bundling tensorflow/basic-pitch is a real, named risk** (spec's Architecture section) — Task 1 is a genuine spike with a pass/fail outcome, not a formality. If it fails, STOP and report back rather than forcing the rest of the plan through on a broken foundation — this is exactly the kind of plan-defect discovery the controller needs to rule on, not something to route around silently.

## File Structure

```text
apps/desktop/
  src-tauri/
    Cargo.toml
    tauri.conf.json
    src/main.rs
    resources/              # PyInstaller --onedir output staged here at build time (gitignored)
  dist/
    index.html               # static placeholder page
  build-backend.sh             # runs PyInstaller, stages output into src-tauri/resources/
  aura_api.spec                # PyInstaller spec file (if needed beyond a CLI invocation)
```

---

## Task 1: PyInstaller spike — bundle `apps/api` standalone

**Files:**
- Create: `apps/desktop/build-backend.sh` (or equivalent build script)
- Create: `apps/desktop/aura_api.spec` (if the bundle needs a spec file rather than a bare CLI invocation — determine during the spike)

**Interfaces:**
- Produces: a standalone directory (PyInstaller `--onedir` output) containing an executable that, when run directly with no `uv`/Python/`.venv` active on `PATH`, starts the FastAPI app on the fixed port (Global Constraints) and serves `GET /healthz`.

This task has a real pass/fail outcome, not a checklist to complete regardless of result.

- [ ] **Step 1: Install PyInstaller into the project's environment**

`uv add --package aura-api --dev pyinstaller` (or equivalent — confirm the correct `uv` invocation for adding a dev-only dependency to a specific workspace package; read `apps/api/pyproject.toml` first to see its current `[project.optional-dependencies]` shape before deciding whether this belongs there or in a separate desktop-specific dependency group).

- [ ] **Step 2: Write a bundle entrypoint**

PyInstaller needs a concrete Python entrypoint script (not just `aura_api.main:app`, since that's an ASGI app object, not a runnable script). Write a small script (e.g. `apps/desktop/run_backend.py`) that imports `uvicorn` and `aura_api.main:app` and calls `uvicorn.run(app, host="127.0.0.1", port=<fixed-port>)` — read `apps/api/src/aura_api/main.py` first to confirm the exact import path. This script is what PyInstaller actually bundles.

- [ ] **Step 3: Run PyInstaller in `--onedir` mode**

```bash
uv run --package aura-api pyinstaller --onedir --name aura-backend apps/desktop/run_backend.py
```
(adjust flags/paths as needed once you see real output — this is a starting point, not a guaranteed-correct invocation). Expect this to take real wall-clock time (tensorflow is a large dependency tree) and expect PyInstaller's hidden-import detection to plausibly miss something on the first attempt — tensorflow/basic-pitch are known to sometimes need explicit `--hidden-import` or `--collect-all` flags. Iterate on the spec/flags based on real errors, don't guess blindly.

- [ ] **Step 4: Verify the bundle runs standalone**

In a shell with `uv`/the repo's `.venv` NOT on `PATH` (e.g. `env -i PATH=/usr/bin:/bin dist/aura-backend/aura-backend`, or a more targeted approach — confirm a way to genuinely exclude the project's Python environment from the check), run the bundled executable and confirm:
```bash
curl -s http://127.0.0.1:<fixed-port>/healthz
```
returns `{"status":"ok"}`. This is the actual proof PyInstaller bundling worked — not "PyInstaller exited 0."

- [ ] **Step 5: Report real findings**

If the spike succeeds: commit the working build script/spec file, note the real bundle size and startup time observed (both relevant for later UX decisions, even though not required to fix here).

If the spike fails after reasonable iteration (a few real attempts, not one try): STOP. Do not proceed to Task 2. Report exactly what failed, what was tried, and treat this as a plan-blocking finding for the controller to rule on (the spec's `--onedir`-as-resource architecture may need to change — e.g. to `--onefile` despite its startup-latency cost, or a different bundler entirely).

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/build-backend.sh apps/desktop/run_backend.py apps/desktop/aura_api.spec apps/api/pyproject.toml uv.lock
git commit -m "feat(desktop): PyInstaller bundle of the backend (spike, verified standalone)"
```

(adjust file list to whatever Step 1-3 actually produced.)

---

## Task 2: Tauri project scaffold

**Files:**
- Create: `apps/desktop/src-tauri/` (via official scaffolding tool, not hand-written)
- Create: `apps/desktop/dist/index.html`

**Interfaces:**
- Produces: a Tauri project that builds and runs (`tauri dev`) showing the static placeholder page — no backend integration yet, that's Task 3.

- [ ] **Step 1: Confirm the Tauri CLI and scaffold via the official generator**

Check what's available: `cargo install tauri-cli --version "^2" --locked` (or check if it's already available; confirm the actual major version — this plan assumes Tauri v2 based on it being the current stable line, but verify rather than assume) then `cargo tauri init` or `npm create tauri-app@latest` from within `apps/desktop/` — use whichever the installed tooling's own guidance recommends. Let the generator produce `Cargo.toml`/`tauri.conf.json`/`src/main.rs` rather than hand-writing them from memory.

- [ ] **Step 2: Replace the generated placeholder frontend with a minimal static page**

Replace whatever the generator scaffolded for `dist/` (or wherever `tauri.conf.json`'s `frontendDist` points) with a minimal `index.html` containing just enough to prove the window renders — plain text is fine, no styling required (styling is sub-project 3's job). Confirm `tauri.conf.json`'s dev/build frontend paths actually point at this file.

- [ ] **Step 3: Verify `tauri dev` launches a window**

Run `cargo tauri dev` (or the generator's equivalent command) and confirm a native window opens showing the placeholder content. This sandbox may or may not have a display server available for a real GUI window to render — if `tauri dev` fails specifically because no display/windowing system is available (not a build error), note this clearly and fall back to verifying via `cargo build` (compiles cleanly) plus `cargo tauri build` producing a real package (Task 6 covers the full build-and-launch-headless verification another way) — don't force a GUI-dependent check to "pass" by faking it.

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/
git commit -m "feat(desktop): scaffold Tauri project with static placeholder page"
```

---

## Task 3: Sidecar spawn + health-check-gated window load

**Files:**
- Modify: `apps/desktop/src-tauri/src/main.rs` (or wherever the generator put the setup hook)
- Modify: `apps/desktop/src-tauri/tauri.conf.json` (to declare the PyInstaller output as a bundled resource)

**Interfaces:**
- Consumes: Task 1's PyInstaller bundle (staged into `src-tauri/resources/` per the build script), Task 2's scaffolded project.
- Produces: on app launch, the bundled backend executable is spawned as a child process before the window opens; the window only opens once `GET /healthz` succeeds.

- [ ] **Step 1: Wire the PyInstaller output into Tauri's resource bundling**

`tauri.conf.json` has a `bundle.resources` field (or the current version's equivalent — confirm the exact field name against the installed Tauri version's own schema/docs rather than assuming) for shipping arbitrary files/folders inside the packaged app. Point it at Task 1's `--onedir` output directory. Update `apps/desktop/build-backend.sh` so it stages that output into the path `tauri.conf.json` expects, and confirm the staging step actually runs before `tauri build`/`tauri dev` needs the files (order matters).

- [ ] **Step 2: Spawn the child process in a setup hook**

In the Rust entrypoint, before the window is created, resolve the bundled executable's real on-disk path via Tauri's resource-resolution API (confirm the exact API — this is exactly the kind of thing Global Constraints says not to guess), and spawn it as a child process with the fixed port (Global Constraints) passed appropriately (CLI arg or env var — match whatever Task 1's `run_backend.py` entrypoint actually accepts).

- [ ] **Step 3: Poll the health endpoint before opening the window**

Poll `http://127.0.0.1:<fixed-port>/healthz` at a short interval with a bounded total timeout generous enough for real tensorflow import time (Task 1's Step 5 should have given you a real observed number to base this on — use it, don't guess a value). On success, proceed to open the window at `http://127.0.0.1:<fixed-port>/` (serving Task 2's placeholder — wait, the placeholder is currently served by Tauri's own webview frontend, not the backend; decide here whether the window loads the LOCAL static file (simplest, and what Task 2 already verified) or a route the backend itself serves — the spec says "opens a native window loading a placeholder page once the backend is healthy," which only requires the health check to gate window creation, not that the backend serves the page content. Keep it simple: window still loads the local static `dist/index.html` from Task 2; that page's own JS fetches `/healthz` from the running backend and displays the result, proving the pipe works end-to-end without needing the backend to serve the page itself). On timeout, do not silently show a broken window — surface an explicit error (a Tauri dialog, or an error state baked into the placeholder page content).

- [ ] **Step 4: Update the placeholder page to show live backend status**

`dist/index.html` (Task 2) should now `fetch('http://127.0.0.1:<fixed-port>/healthz')` on load and render the result — this is the actual end-to-end proof the whole pipe works, not just that a window opened.

- [ ] **Step 5: Verify end-to-end**

Run the app (however Task 2 established works in this sandbox — `tauri dev` if a display is available, or a built package run headlessly otherwise). Confirm: the bundled backend process starts (check via `ps` that a new process matching the bundled executable's name appears), the health check succeeds, and the placeholder page displays live status fetched from the real running backend — not a hardcoded string.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/
git commit -m "feat(desktop): spawn bundled backend as sidecar, gate window on health check"
```

---

## Task 4: Real per-OS app-data path

**Files:**
- Modify: `apps/desktop/src-tauri/src/main.rs` (the setup hook from Task 3)

**Interfaces:**
- Produces: `AURA_DATA_DIR` and `DATABASE_URL` passed to the spawned child process are computed from Tauri's real app-data-dir resolution, not a hardcoded `./data` path.

- [ ] **Step 1: Resolve the real app-data path**

Confirm the exact Tauri API for this (app-data-dir resolution — the exact function/method name varies by Tauri version; check against what's actually installed, per Global Constraints). Ensure the directory exists (`create_dir_all` or equivalent) before the child process needs it — this closes the loop on sub-project 1's carried-forward "nothing creates `./data`" gap, this time for real, at the point where the real path is actually known.

- [ ] **Step 2: Pass it to the child process**

Set `AURA_DATA_DIR=<resolved-path>` and `DATABASE_URL=sqlite:///<resolved-path>/aura.db` as environment variables on the spawned child process (Task 3's spawn call) instead of whatever placeholder was there before.

- [ ] **Step 3: Verify**

Run the app, confirm (via the backend's real behavior — e.g. successfully creating a project and seeing a file appear under the resolved path, or simply confirming the SQLite file gets created at the expected real per-OS location rather than a relative `./data`) that the path is genuinely the platform-appropriate one, not a repo-relative path that happens to work by accident.

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/
git commit -m "feat(desktop): resolve real per-OS app-data path for the sidecar"
```

---

## Task 5: Clean shutdown — no orphaned child process

**Files:**
- Modify: `apps/desktop/src-tauri/src/main.rs`

**Interfaces:**
- Produces: quitting the Tauri app terminates the spawned backend child process; a hard-kill of the Tauri process itself does not leave the child running (verify this specific case, not just clean quit).

- [ ] **Step 1: Wire a shutdown handler**

Confirm the correct Tauri event/hook for app exit (window-all-closed, or an explicit exit event — depends on the installed version's API) and send the child process a termination signal from it.

- [ ] **Step 2: Verify clean quit**

Launch the app, confirm the backend child process is running (`ps`), quit the app normally, confirm the child process is gone (`ps` again, not just "the app looked like it closed").

- [ ] **Step 3: Verify hard-kill doesn't orphan the child**

Launch the app, `kill -9` the Tauri process directly (simulating a crash), then check whether the backend child process survives. If Tauri's own process-group/child-process supervision doesn't handle this automatically, this may need explicit handling (e.g. process-group spawning so a SIGKILL to the parent's group also reaches the child) — report the real observed behavior either way; don't assume Tauri "just handles it."

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/
git commit -m "feat(desktop): terminate sidecar cleanly on app exit, verified against hard-kill"
```

---

## Task 6: `tauri build` — real Linux package, fresh-environment verification

**Files:**
- None expected beyond build-script fixes discovered along the way.

**Interfaces:**
- Produces: a real, installable/runnable Linux package (whatever format `tauri build` targets by default on Linux — confirm during this task) that launches successfully with no `uv`, `python`, or this repo's `.venv` on `PATH`.

- [ ] **Step 1: Run the full build**

```bash
cd apps/desktop && (Task 1's build-backend.sh, to regenerate the PyInstaller bundle fresh) && cargo tauri build
```

- [ ] **Step 2: Verify in a genuinely clean environment**

Run the built package's executable in a shell/environment where `uv`, `python3`, and this repo's `.venv` are NOT on `PATH` (a real check, e.g. `env -i PATH=/usr/bin:/bin <built-executable-path>` or spawning it from a directory/user context that can't see the project's Python setup). Confirm the window opens, the health check succeeds, and the placeholder shows live status — the actual proof that packaging is self-contained, which is this whole sub-project's point.

- [ ] **Step 3: Report package size and any build warnings**

Not blocking, but worth recording for later reference (tensorflow bundling is known to produce large binaries).

- [ ] **Step 4: Commit**

Only if Step 1-2 required build-script/config fixes beyond what's already committed.

---

## Task 7: Full verification + docs update

**Files:**
- Modify: `docs/superpowers/SESSION-HANDOFF.md`

- [ ] **Step 1: Re-run every prior task's verification step in sequence**

Confirm nothing regressed across tasks (e.g. Task 4's app-data-dir change didn't break Task 3's health-check timing, Task 5's shutdown handling still works after Task 6's build changes).

- [ ] **Step 2: Update `docs/superpowers/SESSION-HANDOFF.md`**

Mark sub-project 2 (desktop shell + packaging) done — spec/plan paths, what was verified on Linux, explicit note that macOS/Windows were NOT build-verified in this repo's history (don't let that caveat get lost). Point at sub-project 3 (score preview + playback UI) as next.

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/SESSION-HANDOFF.md
git commit -m "docs: mark desktop shell + packaging sub-project done"
```

## Definition of Done

- PyInstaller bundles `apps/api`'s full real dependency tree (verified: real tensorflow/basic-pitch import succeeds in the bundle, not just a trivial script).
- A Tauri app spawns that bundle as a child process, gates window creation on a real `GET /healthz` success, and shows live backend status on the placeholder page.
- Quitting the app (both clean quit and a simulated hard-kill) leaves no orphaned backend process — verified via process inspection, not assumed.
- `AURA_DATA_DIR`/`DATABASE_URL` resolve to a real per-OS path via Tauri's own API, not a repo-relative placeholder.
- `tauri build` produces a Linux package that runs with no Python toolchain on `PATH` — verified in a genuinely clean environment.
- macOS/Windows are configured but explicitly and honestly documented as not build-verified in this repo.
