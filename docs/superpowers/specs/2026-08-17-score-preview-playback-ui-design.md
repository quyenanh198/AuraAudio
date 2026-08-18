# Score Preview + Playback UI — Design

## Context

Sub-project 3 of 4 in the offline-desktop-app pivot
(`docs/superpowers/SESSION-HANDOFF.md`, "Direction change"). Sub-projects 1-2
(merged to `main`) delivered a fully offline backend (SQLite, local blob
storage, in-process job dispatch) wrapped in a Tauri v2 shell that spawns the
bundled backend as a health-gated sidecar. The only UI today is a static
placeholder page (`apps/desktop/web/index.html`) whose sole job is proving the
pipe works. This sub-project replaces it with the first real product surface:
upload a clip, watch it transcribe, see the rendered notation, play it back
with a synced cursor, export. Editing is explicitly sub-project 4.

Layout direction was settled visually with the user on a design canvas
(five artboards: three options, the home screen, and the approved hybrid):
https://claude.ai/code/artifact/e595c5db-c61d-48ce-82be-6b92fde37551 —
working files under `.superpowers/design/score-view/` (gitignored). The
approved direction is the **"B + A hybrid"** board; the visual language is
dark UI / warm paper score / single amber accent, as drawn there.

Facts verified against the current code before writing this spec:

- Backend routes today: `POST /v1/uploads` (multipart), `POST /v1/projects`,
  `POST /v1/projects/{id}/transcriptions`, `GET /v1/jobs/{id}`,
  `GET /v1/exports/{id}` + `/download`. There is **no** project list, no
  score-JSON endpoint, and no audio endpoint — all three are new here.
- Score schema v4 events carry `onsetSeconds`/`offsetSeconds` (performed
  time) alongside notated time; the pipeline's `assign` stage writes the
  final score JSON as a blob artifact (`jobs/{id}/stage/assign.json`), and
  the `normalize` stage writes the normalized WAV artifact.
- CORS (from sub-project 2's security fix): `apps/desktop/run_backend.py`
  composes a root Starlette app where ONLY `/healthz` is CORS-enabled
  (wildcard); the mounted `aura_api.main` app has no CORS — arbitrary local
  webpages cannot read `/v1/*` responses. A drift-detection test pins the
  `/healthz` duplication (`apps/desktop/tests/test_cors_scope.py`).
- The Tauri shell (Rust `backend.rs`) health-gates window visibility, spawns
  the bundled backend on fixed port 8317, resolves a real per-OS app-data
  dir, and terminates the child gracefully on quit. None of that changes here.
- `apps/desktop/web/` is plain static files; `tauri.conf.json`'s
  `frontendDist` is `../web`. No Node build step exists anywhere yet.

## Goal

From a fresh app launch, a user can: see their past transcriptions (title,
instrument, date, status) and start a new one by dropping/browsing an audio
file; watch progress while the pipeline runs; open a finished project to a
rendered score (guitar tab or piano grand staff); play back either the
original recording or a synthesized rendition of the transcription with a
cursor tracking the notation, toggle between the two mid-playback at the same
position; and export MusicXML/MIDI. All fully offline.

## Non-Goals (deferred)

- **Editing anything** — notes, durations, locks, undo/redo. Sub-project 4.
- **PDF export** — still future work per the handoff backlog; export buttons
  cover the two formats the backend produces today (MusicXML, MIDI).
- **Project management beyond listing** — no rename, no delete, no search.
  The list shows what exists; management UI is future scope.
- **Waveform display** — the transport shows a plain progress bar, not a
  rendered waveform. (The normalize stage's waveform-proxy idea from
  `ARCHITECTURE.md` stays unused for now.)
- **Synth realism** — the synthesized source is a sampler playing per-event
  pitches at performed times; velocity nuance, sustain-pedal modelling, and
  guitar articulations are out of scope.
- **Windows/macOS verification** — same posture as sub-project 2: Linux is
  the verified platform in this repo; others are configured but unverified.
- **State-management library** — plain Svelte stores. Revisit in
  sub-project 4 only if editing state (undo stacks, selections) demands it.

## Architecture

### Frontend stack and layout

`apps/desktop/web/` becomes a Svelte + Vite TypeScript app (the existing
static `index.html` and its health-fetch script are replaced; the Rust-side
health gating is untouched). Tauri wiring is the standard pattern:
`build.beforeDevCommand` runs Vite's dev server for `tauri dev`,
`build.beforeBuildCommand` runs `vite build`, and `frontendDist` points at
Vite's output directory. This adds a Node/npm build step to the desktop
app — unavoidable with any framework, accepted in the framework decision.

Two routes:

- **Home** (`/`) — per the Home artboard: top bar (wordmark + "New
  transcription"), a drop zone (drag-and-drop + click-to-browse), and the
  recent-projects list. Each row: instrument icon, title, facts line,
  status chip (or a progress bar while a job runs). Rows poll
  `GET /v1/jobs/{id}` while a job is non-terminal.
- **Score** (`/project/:id`) — per the approved hybrid artboard: collapsible
  left sidebar (project meta, detection facts — key/tempo/meter/instrument —
  view options: tab visibility, zoom; export buttons), notation area
  center-right on the warm-paper surface, docked bottom transport bar
  (skip-to-start, play/pause, elapsed/total time, scrubber, the
  Recording/Synth source toggle, volume). No top transport strip.

State lives in plain Svelte stores, split by concern: `projects` (list +
polling), `score` (fetched score JSON + MusicXML for the open project), and
`playback` (position, playing, active source, volume). Components read
stores; fetch logic lives in a small `api.ts` client module wrapping the
`http://127.0.0.1:8317` base URL (fixed port, from sub-projects 1-2).

### Backend additions (all additive, no existing route changes)

- `GET /v1/projects` — list, newest first. Each item: `id`, `title`,
  `instrument`, `created_at`, `duration_ms` (from the media asset), the
  latest transcription job's `{id, status, stage, progress}` so Home can
  render status chips and progress without N+1 calls, and — when that job
  succeeded — its exports as `[{id, format}]`. The exports field exists
  because no list-exports route does: without it the frontend cannot
  discover export IDs at all (sub-project 1's own smoke test had to read
  them from SQLite directly). The score view fetches this same list to
  resolve its MusicXML (for rendering) and its export-button targets.
- `GET /v1/projects/{id}/score` — the latest score JSON: resolved by finding
  the project's latest succeeded job's `assign`-stage artifact and returning
  the blob's JSON. 404 when no succeeded job exists.
- `GET /v1/projects/{id}/audio` — the normalized WAV artifact for the same
  job, served via `FileResponse` (identical pattern to the export download
  route, including the defensive `path_for` containment + `is_file` checks).

### CORS: origin-scoped allowlist for the webview

The webview must call `/v1/*` cross-origin, which sub-project 2's fix
deliberately blocks. The change (in `apps/desktop/run_backend.py` only — the
shared `aura_api.main` app stays untouched, same discipline as before): add
CORS middleware for `/v1/*` restricted to an **exact-origin allowlist** —
the Tauri webview origin(s) as actually observed at runtime (verify
empirically during implementation: Tauri v2 on Linux/WebKitGTK typically
`tauri://localhost` or `http://tauri.localhost`; do not trust memory) plus
Vite's dev-server origin for `tauri dev`. Never a wildcard: an arbitrary
local webpage's `Origin` won't match the allowlist, so `/v1/*` responses
remain unreadable to drive-by pages — preserving the security property the
final review of sub-project 2 established. The wildcard stays confined to
`/healthz` exactly as today. The existing CORS-scope test extends to pin the
new behavior: allowlisted origin gets headers on `/v1/*`; a foreign origin
(e.g. `http://evil.example`) does not.

### Notation rendering: OpenSheetMusicDisplay

OSMD (TypeScript, npm) renders the project's MusicXML — fetched via the
existing export download route — into the notation area. Chosen over Verovio
for direct MusicXML input, first-class cursor API, and clean Vite
integration; engraving quality is adequate for a preview surface. **Plan
Task 1 is a real spike**: render this repo's actual exported MusicXML (a
guitar-tab fixture and a piano grand-staff fixture, produced by the real
pipeline) in OSMD inside the real webview, and exercise the cursor API. If
tab or grand-staff rendering is unacceptable, the spike stops the plan for a
re-decision (Verovio is the named fallback) rather than building on a broken
foundation — same discipline as sub-project 2's PyInstaller spike.

### Playback + cursor sync

One sync design shared by both sources:

- **Timeline build (pure function, unit-tested):** the score JSON's events
  in document order match the MusicXML's note order (both generated from the
  same score by `musicxml/export.py`). At load, walk OSMD's cursor from the
  start, pairing each cursor step with the corresponding event's
  `onsetSeconds`, producing a sorted timeline `[(t_i, step_i)]`. This
  order-matching assumption is asserted by the spike against real pipeline
  output, not assumed.
- **Clock:** a `requestAnimationFrame` loop reads the active source's
  current time and moves the cursor to the last timeline entry with
  `t_i <= now`, auto-scrolling it into view. Binary search over the
  timeline; rewind/seek is the same lookup.
- **Recording source:** an `<audio>` element pointed at
  `GET /v1/projects/{id}/audio`. Native seeking, volume.
- **Synth source:** a sampler library (`smplr`-class) with two instrument
  soundfonts (acoustic piano, acoustic guitar) **bundled locally** in the
  app — zero runtime network, matching the offline constraint (verify the
  chosen library can load local soundfont data; that's part of the spike).
  Events are scheduled at their `onsetSeconds` (performed time — the
  transcription's own timing record), so both sources share one clock
  semantics and A/B comparison is like-for-like.
- **Toggle:** switching source pauses the old source, seeks the new one to
  the current position, and resumes if playing. Position is the single
  source of truth in the `playback` store.

### Export

The sidebar's MusicXML/MIDI buttons call the existing
`GET /v1/exports/{id}` → `/download` routes. In the webview, downloads
triggered from `fetch`ed blobs need Tauri's dialog/fs path (the sandboxed
webview cannot always use plain `<a download>`) — verify the working
mechanism during implementation (Tauri v2's dialog plugin + fs write, or the
webview's native download handling, whichever actually works in WebKitGTK);
the spike-first rule applies to this too if it proves fragile.

## Error Handling

- **Job failures:** Home rows and the score view render the backend's real
  `error_code`/`error_detail` (already stored per job by the pipeline) with
  a retry affordance (re-`POST /v1/projects/{id}/transcriptions` — the
  backend's idempotency handling already returns the existing job or makes a
  new one appropriately).
- **Fetch failures** (backend briefly unreachable, 404 score before a job
  finishes): inline error/empty panels in place of the affected region —
  never a blank screen, never a dead spinner. The health-gated window means
  the backend is up at launch; mid-session death is out of scope to auto-heal
  (matches sub-project 2's posture).
- **Unsupported files** dropped on the drop zone: the upload endpoint's
  existing 422 surfaces as an inline message naming the accepted formats.

## Testing

- **Unit (Vitest):** the timeline-build function (score JSON → sorted
  timeline; edge cases: chords sharing an onset, empty score, out-of-order
  input rejected), the binary-search cursor lookup, and the `playback`
  store's toggle/seek semantics. These are pure logic — no OSMD, no audio.
- **Spike evidence (Task 1):** real exported guitar-tab and piano
  grand-staff MusicXML rendered by OSMD in the real webview, cursor API
  exercised, screenshots as evidence — the plan's foundation gate.
- **Backend (pytest, existing patterns):** the three new endpoints get
  route tests in the established `apps/api/tests` style (real
  `LocalStorageClient` via monkeypatch, real DB fixtures); the CORS
  allowlist behavior extends `apps/desktop/tests/test_cors_scope.py`
  (allowlisted origin passes, foreign origin gets no CORS headers on
  `/v1/*`, `/healthz` wildcard unchanged).
- **End-to-end (Xvfb, established pattern):** one full-flow check in the
  real Tauri app — launch, upload a fixture through the real UI, wait for
  transcription, confirm notation renders (screenshot), confirm playback
  starts and the cursor moves, confirm export produces a file. Manual-ish
  scripted verification as in sub-projects 1-2, not a brittle CI suite.

## Definition of Done

- From a packaged (`tauri dev` at minimum; `tauri build` for the final
  check) app: the full journey — drop file → watch progress → score renders
  (both instruments verified via fixtures) → play Recording with moving
  cursor → toggle to Synth mid-playback at the same position → export
  MusicXML and MIDI to real files — works offline, verified per the Testing
  section.
- The three new backend endpoints exist with tests; no existing route
  changed shape.
- CORS on `/v1/*` admits exactly the webview/dev origins and nothing else —
  pinned by tests; `/healthz` behavior unchanged.
- Unit tests cover the sync timeline logic; the OSMD spike's evidence is
  recorded in its task report.
- `docs/superpowers/SESSION-HANDOFF.md` updated (done + pointer to
  sub-project 4), with the honest wording convention (no "DONE" before the
  final whole-branch review has actually run).
