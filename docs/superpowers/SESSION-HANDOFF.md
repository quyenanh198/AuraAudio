# Session Handoff

Read this first in any new session picking up AuraAudio work. It exists so a
fresh session (with no memory of prior conversations) can orient in one read
instead of re-deriving decisions from `git log`.

## Direction change (read this before anything else below)

The user has redirected the project away from `ARCHITECTURE.md`'s original
cloud-service client model (web app + Postgres + S3, Phase 2 item 4 = "web
client + SVG preview", Phase 3 = separate later editing effort) toward a
**fully offline desktop app**, with editing pulled forward into scope
rather than deferred. The backend transcription pipeline built in Phase 1
and Phase 2 sub-projects 1-3 (below) is unaffected and is being reused as-
is — this is a client/packaging/storage pivot, not a rewrite of the
algorithms. `ARCHITECTURE.md`'s phase numbering is now superseded for
everything client-facing; treat it as historical record of the backend
design rationale (still accurate for probe/inference/structure/quantize/
assign/export), not as the current plan for what ships next.

**Decisions made so far** (via `superpowers:brainstorming`, architectural
path, not yet written to a spec file):
- Reuse the existing FastAPI + SQLAlchemy backend, bundled and run locally
  as a sidecar process — not rewritten in another language. Swap
  Postgres → SQLite, S3 → local filesystem.
- Desktop shell: **Tauri** (Rust shell + system webview), chosen over
  Electron (better UI/UX ceiling via bundled-Chromium consistency, but
  larger footprint) and pywebview (simplest, but weakest fit for a
  rich interactive score-editing surface). Adds a Rust toolchain
  alongside the existing Python one.
- Scope: **full editing pulled forward** into this effort, not deferred —
  the user explicitly chose this over a preview-only v1, so the semantic
  edit-operation model (originally Phase 3) is now part of the plan, not a
  future phase.

**Decomposition into sequential sub-projects** (approved by the user,
dependency-ordered — each gets its own brainstorm → spec → plan cycle,
same process as the three backend sub-projects below):
1. **Offline backend adaptation** — Postgres→SQLite, S3→local filesystem,
   confirm the existing pipeline runs fully offline as a standalone local
   process. Backend-only, no UI. **DONE (2026-08-16), including final whole-branch review and its fix wave** (one Critical security finding — path traversal via upload filename — plus 3 Important cross-task gaps, all fixed; one residual regression the fix itself introduced was caught by the scoped re-review and closed with a targeted follow-up commit). Spec:
   `docs/superpowers/specs/2026-08-16-offline-backend-adaptation-design.md`;
   plan: `docs/superpowers/plans/2026-08-16-offline-backend-adaptation.md`;
   executed via
   `subagent-driven-development`. See "Offline desktop app sub-projects"
   below for what changed and how it was verified.
2. **Desktop shell + packaging** — Tauri wrapper spawning the Python
   backend as a managed sidecar, native window at localhost. No real UI.
   **DONE (2026-08-17), including final whole-branch review and both its
   fix waves** (3 Important cross-task findings — a health gate that
   trusted port liveness over child liveness, `apps/desktop/tests` unwired
   from any CI entrypoint, a shutdown signal severity backwards from the
   crash path — plus several cheap cleanups, all fixed; the scoped
   re-review then caught the health-gate fix hadn't actually closed its
   target scenario, plus a new pid-reuse hazard the fix itself introduced,
   both closed in one small targeted follow-up, independently
   re-verified). Spec:
   `docs/superpowers/specs/2026-08-16-desktop-shell-packaging-design.md`;
   plan: `docs/superpowers/plans/2026-08-16-desktop-shell-packaging.md`.
   See "Offline desktop app sub-projects" below for what changed and how
   it was verified.
3. **Score preview + playback UI** — upload flow, SVG notation rendering
   (likely via an existing MusicXML-to-SVG renderer rather than building
   one from scratch — not yet decided) synced to audio playback, export.
   Real product/UI design work; plan to use the brainstorming skill's
   visual companion tool for this one. **Next up now that sub-project 2 is
   done** — no spec or plan exists yet; start with
   `superpowers:brainstorming`.
4. **Semantic editing** — edit-operation model (add/delete/move note,
   undo/redo, optimistic locking, locks) plus the UI to drive it. Biggest
   single piece, builds on 1-3.

Sub-project 1 is DONE, including final whole-branch review and its fix wave (see above).
Sub-project 2 is DONE, including final whole-branch review and both its fix waves (see above).
Sub-projects 3-4 have no written spec yet — only the scoping/technology
decisions above have been made in conversation.

## What AuraAudio is

Converts an uploaded guitar/piano audio clip into an editable score
(MusicXML + MIDI). Full product design lives in `ARCHITECTURE.md` (repo
root) — a 4-phase plan, **now superseded for the client/packaging model,
see "Direction change" above; still accurate for the backend pipeline.**
**Phase 1 (vertical slice) is done and merged.** Phase 2's backend
intelligence work (sub-projects 1-3 below) is done. Phase 2's original
client plan (item 4) and Phase 3 (editing) have been superseded by the
offline desktop app direction above.

## Repo layout

```text
apps/api/                      FastAPI service: projects, jobs, exports
workers/transcription/          Worker: probe -> normalize -> inference ->
                                structure -> quantize -> assign -> export
packages/score_schema/          Canonical score JSON contract (schemaVersion 4)
packages/musicxml/               MusicXML export + reopen validation
packages/test_fixtures/          Synthetic audio generators for tests
infra/docker-compose.yml         Postgres, Redis, MinIO (real deployment)
docs/superpowers/specs/          Design docs, one per sub-project
docs/superpowers/plans/          Implementation plans, one per sub-project
```

## Status: what's done

**Phase 1 — transcription vertical slice.** Upload -> job -> probe ->
normalize -> inference (basic-pitch) -> quantize -> export (MIDI +
MusicXML), full FastAPI + worker + object storage pipeline. Plan:
`docs/superpowers/plans/2026-08-15-transcription-vertical-slice.md`.

**Phase 2, sub-project 1 — beat/meter/key detection.** Replaced the
hardcoded 120 BPM / 4/4 grid with real detection: a new `structure` worker
stage (tempo + beats via `librosa.beat.beat_track`, meter via a validated
accent-periodicity scorer over `{"4/4", "3/4"}` only, key via `music21`'s
`analyze('krumhansl')`), score schema bumped to v2 (`tempoBpm`/`meter`/
`key`/`confidence` per part), `quantize` and `musicxml/export.py` consuming
real values with key-aware enharmonic spelling. Spec:
`docs/superpowers/specs/2026-08-15-beat-meter-key-detection-design.md`.
Plan: `docs/superpowers/plans/2026-08-16-beat-meter-key-detection.md`.

**Phase 2, sub-project 2 — guitar string/fret assignment. DONE, including
final whole-branch review and its fix wave.** Score schema bumped to v3
(optional `string`/`fret` per event). New `aura_worker.fingering` module:
candidate generation, chord assignment via backtracking (distinct-string
hard constraint, minimize hand stretch, then prefer the lowest-fret
position among ties), sequence DP (Viterbi-style) over a measure via
`assign_measure`. New `assign` worker stage between `quantize` and
`export` (guitar: runs the algorithm; piano: passthrough). MusicXML export
renders real tab notation via `music21`'s
`StringIndication`/`FretIndication`, with the verified numbering conversion
`musicxml_string = 6 - internal_string` (independently re-verified three
separate times across the sub-project). Spec:
`docs/superpowers/specs/2026-08-16-guitar-fret-assignment-design.md`. Plan:
`docs/superpowers/plans/2026-08-16-guitar-fret-assignment.md`.
Manual smoke test + a committed e2e assertion both confirm tab data reaches
the real exported MusicXML end-to-end via the real API+worker pipeline.
The SDD workspace (`.superpowers/sdd/2026-08-16-guitar-fret-assignment/`)
has been deleted — this sub-project's process is fully closed out.

Two things worth knowing about (not current issues, already resolved):
1. Task 1's schema v2→v3 bump broke a pre-existing test's literal
   `schemaVersion == 2` assertion in `test_quantize.py` (that file wasn't
   in Task 1's own file list, so its own suite run didn't catch it) —
   caught by Task 2's full-suite run, fixed directly on main same session.
2. The final whole-branch review found 3 Important (non-blocking) gaps,
   all fixed in one follow-up commit: (a) `assign_chord`'s tie-break among
   equal-stretch chord voicings now also prefers the lowest fret position
   (previously some open-position chords could land needlessly high on the
   neck — e.g. C major at frets 8-10 instead of an equally-valid 3-5); (b)
   the spec's distinct-string property test (previously only run ad hoc by
   a reviewer) is now a committed, seeded, 750-trial test; (c) the existing
   e2e pipeline test now asserts `<technical>` actually appears in the
   exported MusicXML, not just that the MIDI export succeeds — this
   assertion was stress-tested by deliberately breaking the assign->export
   seam and confirming the test catches it. 6 further Minor findings were
   reviewed and explicitly parked (pre-existing precedent or out of scope
   per the spec's Non-Goals) — not re-litigated in a future sub-project.

**Phase 2, sub-project 3 — piano hand/staff assignment. DONE, including
final whole-branch review and its fix wave.** Score schema bumped to v4
(optional `hand`: `"left"`/`"right"`/`null` per event). New
`aura_worker.piano_hands` module: candidate split generation (per onset,
every way to divide the chord's sorted pitches between hands is a valid
candidate — no hard "unreachable" case, unlike guitar frets), sequence DP
(Viterbi-style, same shape as guitar's `assign_measure`) minimizing hand
movement + a soft hand-span penalty + a *weak* pull toward middle C (per
`ARCHITECTURE.md`'s "middle-C is a weak prior, not a hard boundary").
`assign` worker stage's previously-no-op piano branch now fills in real
`hand` values (guitar's branch untouched; both branches coexist in the
same file via aliased imports — `assign_string_fret`/`assign_hands` — to
avoid a real function-name collision between `aura_worker.fingering` and
`aura_worker.piano_hands`, both of which export `assign_measure`).
MusicXML export gained a genuinely different rendering path for piano: two
`music21.stream.PartStaff` objects (treble/right=staff 1, bass/left=staff
2) grouped into one real grand staff via `layout.StaffGroup(symbol="brace")`
— verified directly against real `music21` output (staff dedup of shared
`<attributes>`, tempo-mark-once-not-duplicated, auto-inserted rests for an
empty-handed measure) before any of it was written into the spec or plan.
Out-of-range notes (outside the standard 88-key range, MIDI 21-108) get
`hand: null` in the JSON but are still rendered, clamped to the nearer
staff for display only — the JSON's `null` is never mutated. Spec:
`docs/superpowers/specs/2026-08-16-piano-hand-staff-assignment-design.md`.
Plan: `docs/superpowers/plans/2026-08-16-piano-hand-staff-assignment.md`.
Manual smoke test + committed e2e/unit assertions all confirm the grand
staff reaches the real exported MusicXML end-to-end, with per-note staff
placement pinned (not just staff-count presence) — see point 2 below for
why that distinction mattered. The SDD workspace has been deleted.

Two things worth knowing about (not current issues, already resolved):
1. Same category of regression as sub-project 2's Task 2 finding: Task 1's
   schema v3→v4 bump broke a pre-existing test's literal `schemaVersion ==
   3` assertion in `test_quantize.py` — caught by Task 4's full-suite run,
   fixed directly on main same session.
2. The final whole-branch review found 1 Important (non-blocking) gap: the
   committed e2e test for piano (added proactively in Task 6, specifically
   to avoid the *kind* of gap sub-project 2 found after the fact) turned
   out to still be insensitive to the actual algorithm — it only asserted
   `<staves>2</staves>` presence, which stays true even with piano hand
   assignment completely disabled (every note would clamp to one staff via
   the out-of-range fallback, but the file would still structurally have
   two staves). The reviewer proved this by literally disabling the
   algorithm and watching the test still pass. Fixed by pinning assertions
   to specific notes landing on specific staves (both at the e2e level and
   the `test_export.py` unit level), verified by reproducing the exact
   disable-and-check methodology twice more (once by the fix-wave
   implementer, once independently by the scoped re-reviewer) before
   calling it closed. 6 further Minor findings were reviewed and explicitly
   parked (pre-existing precedent, out of this sub-project's Definition of
   Done, or already-accepted design tradeoffs) — not re-litigated in a
   future sub-project. One parked Minor (a genuinely pre-existing bug,
   predating this sub-project: MusicXML export doesn't sort by
   `notatedOnset`, so exported rhythm can come out in the wrong order for
   *both* instruments) is worth flagging here since it's a real defect —
   just correctly out of scope for this sub-project's structural DoD.
   Worth its own future bounded fix.

Full workspace test suite: **112/112 passing.** Working tree clean, `main`
in sync with `origin/main` (last pushed commit: `eb58113`).

## Phase 2 backend sub-projects (all done — this list is now historical)

1. ~~Beat/meter/key intelligence~~ — **done** (above).
2. ~~Guitar string/fret assignment~~ — **fully done**, including the final
   whole-branch review and its fix wave. See the "Status: what's done"
   section above for full detail.
3. ~~Piano hand/staff assignment~~ — **fully done**, including the final
   whole-branch review and its fix wave. See the "Status: what's done"
   section above for full detail.

All backend transcription-intelligence work is done. What used to be
listed here as "item 4: web client" and a separate PDF-rendering /
benchmark-harness backlog has been superseded by the offline-desktop-app
direction — see "Direction change" near the top of this document and
"Offline desktop app sub-projects" below for the current plan (4
sequential sub-projects; the first, offline backend adaptation, is done;
the second, desktop shell + packaging, is also done — sub-project 3 is
next). PDF rendering and an offline benchmark CI
harness are still real future work, just not sequenced yet; revisit once
the desktop app's 4 sub-projects are further along.

## Offline desktop app sub-projects

The four sub-projects from "Direction change" above, tracked here as they
complete (same pattern as "Phase 2 backend sub-projects" above).

1. **Offline backend adaptation. DONE, including final whole-branch
   review and its fix wave.** Swapped the backend off a cloud-service
   stack (Postgres/Redis/S3/MinIO) onto a fully offline, single-process
   local app: Postgres → SQLite (`DATABASE_URL=sqlite:///./data/aura.db`),
   S3 → a filesystem-backed `LocalStorageClient` (consolidated so the API
   and worker share one implementation instead of two separate S3
   clients), presigned S3 upload/download → direct multipart upload
   (`POST /v1/uploads`) and a direct `FileResponse` download
   (`GET /v1/exports/{id}/download`), Redis/RQ job queue → in-process
   thread-pool dispatch on job creation. `boto3`, `rq`, `redis`, and
   `psycopg2-binary` are gone from both `apps/api` and
   `workers/transcription`'s dependencies; `python-multipart` was added
   to `apps/api`. Both guitar and piano e2e pipeline tests were migrated
   off S3/boto3 mocking onto the new local stack, which incidentally
   surfaced and fixed two real pre-existing bugs along the way: a numpy
   2.x / tensorflow 2.14 ABI incompatibility (`numpy` now pinned `<2` in
   `workers/transcription`) and a job-dispatch race condition in the
   thread-pool path. The final whole-branch review caught one Critical
   finding — a path-traversal vulnerability created by the S3→filesystem
   swap (an unsanitized upload filename / `object_key` could escape the
   blob root; `LocalStorageClient.path_for` now enforces containment) —
   plus 3 Important cross-task gaps (fresh clone couldn't start because
   nothing created `./data`; `source .envrc && make test`, the project's
   own documented workflow, silently wiped the real app's SQLite DB and
   blobs because the test conftests used `setdefault` instead of an
   unconditional override; a doc claim about containerized deployment
   still working was false). All fixed in one dispatched fix wave; the
   scoped re-review of that wave caught one more regression the fix
   itself introduced (a filename of exactly `".."` bypassed the basename
   sanitization and could permanently brick the upload endpoint) — closed
   with one targeted follow-up commit rather than a second full review
   cycle, per this process's "no second fix wave" rule. Full detail in
   the SDD ledger. Spec:
   `docs/superpowers/specs/2026-08-16-offline-backend-adaptation-design.md`.
   Plan: `docs/superpowers/plans/2026-08-16-offline-backend-adaptation.md`.
   All 9 tasks implemented and reviewed clean via
   `subagent-driven-development` (SDD workspace:
   `.superpowers/sdd/2026-08-16-offline-backend-adaptation/` — a brief +
   report per task, plus a final task-9 verification report). Task 9's
   verification: the full workspace suite (126/126 across all five
   packages, both e2e tests included) passes with the Docker daemon down
   and zero Postgres/Redis/MinIO running; a manual smoke test then drove
   the standalone `uvicorn` API process end-to-end through `curl` only —
   upload, project creation, transcription job, polling to `succeeded`,
   and downloading real MIDI + MusicXML export bytes — confirming the app
   works as one local process with no external services.
2. **Desktop shell + packaging. DONE, including final whole-branch review
   and both its fix waves.** Built a
   Tauri v2 wrapper that spawns the existing FastAPI backend (already
   adapted to run fully offline by sub-project 1) as a managed sidecar
   process instead of rewriting any client logic: a PyInstaller `--onedir`
   bundle of `apps/api`'s full real dependency tree, including
   tensorflow/basic-pitch (verified via a real inference smoke test, not
   just a trivial import); a Rust `backend.rs` module that resolves the
   bundled executable via Tauri's resource resolver, spawns it, polls
   `GET /healthz` with a 30s budget, and gates showing the main window on
   a successful health check; CORS scoped to only the `/healthz` route
   (a security fix — the first draft wildcarded CORS across the whole
   backend app, which would have let any local webpage read real
   `/v1/jobs`/`/v1/exports` responses); `AURA_DATA_DIR`/`DATABASE_URL`
   resolved to a real per-OS path via Tauri's own `app_data_dir()` API
   (e.g. `~/.local/share/com.auraaudio.desktop` on Linux), not a
   repo-relative placeholder; a fix for a real gap task 4's own
   verification surfaced — a fresh install had no DB schema at all, only
   the data *directory*, so the first `POST /v1/projects` 500ed — closed
   by an inserted task 4b (`Base.metadata.create_all` on first launch, no
   migration tooling needed yet since there's exactly one schema version
   in play); clean shutdown wired to `RunEvent::ExitRequested` for a
   normal quit, plus a Linux `PR_SET_PDEATHSIG` orphan guard registered in
   the spawned child itself so a hard `kill -9` of the Tauri process still
   reaps the backend (SIGKILL can't be caught by the parent, so nothing
   else could handle this case); and a real `tauri build` Linux `.deb`
   package, verified to run standalone in a genuinely clean container with
   no Python toolchain reachable on `PATH`. **macOS/Windows are configured
   in `tauri.conf.json` but explicitly NOT build-verified anywhere in this
   repo's history** — Linux is the only platform with a real, tested
   artifact; don't let that caveat get lost. RPM packaging was attempted
   and could not be verified in this sandbox (no `rpmbuild` binary
   present) — an environmental limitation, not a defect in the packaging
   config; not worth re-attempting in this same sandbox. Spec:
   `docs/superpowers/specs/2026-08-16-desktop-shell-packaging-design.md`.
   Plan: `docs/superpowers/plans/2026-08-16-desktop-shell-packaging.md`.
   SDD workspace: `.superpowers/sdd/2026-08-16-desktop-shell-packaging/`
   (a brief + report per task, including task 4b, plus this task-7
   re-verification report). Task 7's re-verification (2026-08-17), run
   fresh rather than trusted from prior reports: `cargo build` in
   `apps/desktop/src-tauri` still succeeds cleanly; the full
   `apps/desktop/tests/` suite still passes 4/4 (3 CORS-scoping tests + 1
   schema-init regression test); a live `Xvfb` + `tauri dev` launch still
   spawns the real bundled backend and gates the window on a real health
   check, screenshot-confirmed showing "Backend status: reachable" with
   the live `{"status": "ok"}` body; both shutdown paths were
   re-exercised via real process inspection — a clean quit
   (`xdotool windowclose`) leaves no orphan, and a hard `kill -9` of the
   Tauri process still reaps the backend via the orphan guard — confirming
   no regression across Task 4's app-data-dir change, Task 4b's schema
   init, Task 5's shutdown handling, and Task 6's build changes, which all
   touch overlapping areas of `backend.rs`. One false alarm during
   re-verification, not a real regression: running the desktop test suite
   with `.envrc` sourced first made one CORS test fail (404 instead of an
   expected 200/500), because `run_backend.py`'s test-time env vars use
   `os.environ.setdefault(...)` — with `.envrc` already exporting
   `AURA_DATA_DIR`/`DATABASE_URL`, the test silently targeted the real
   `./data/aura.db` (which already has a schema from earlier manual
   smoke tests) instead of its intended throwaway path. Re-running without
   sourcing `.envrc` (matching how every prior task in this sub-project
   invoked this suite) gave a clean 4/4 pass; no data was affected either
   way since schema creation is idempotent. Fixed for real in the final
   whole-branch review's fix wave (see below), not left as a follow-up.

   **Final whole-branch review** (most capable model, full branch diff):
   no Critical findings, explicit security scan for the path-traversal
   class that shipped in sub-project 1 found nothing comparable here. 3
   Important, all genuinely cross-cutting (exactly what a per-task review
   can't see): (a) the health-check gate trusted "something answered on
   port 8317" rather than "my own spawned child is still alive" — a second
   app instance whose backend died on the already-bound port would have
   its health poll silently succeed against the *first* instance's
   backend; (b) `apps/desktop/tests/` was never wired into `make test` or
   any CI entrypoint, so the schema-init regression test built specifically
   because "nothing would catch this" was itself unreachable by any command
   anyone runs; (c) shutdown signal severity was backwards — clean quit
   sent `SIGKILL` (zero chance for graceful shutdown) while a hard crash of
   the parent ended up delivering the gentler `SIGTERM` via the PDEATHSIG
   guard, latent today but would abort in-flight transcription work the
   moment sub-project 3 exists. All three fixed in one dispatched fix wave
   (`try_wait()`-based child-liveness checking + captured stderr logging;
   tests wired into `make test` together with fixing the `setdefault`
   isolation gap in the same commit, since shipping one without the other
   would have made the gap more likely to bite; SIGTERM-then-bounded-wait-
   then-SIGKILL shutdown, moved to the actually-unconditional
   `RunEvent::Exit`), plus several cheap cleanups (dead Tauri event emits
   removed, stale doc comments fixed, `apps/desktop/data/` gitignored, the
   generated PyInstaller spec file documented as such, a real `// SAFETY:`
   invariant written for the process's one `unsafe` block, a `/healthz`
   drift-detection pinning test added). The scoped re-review of that fix
   wave then caught two real residual issues — the health-gate fix hadn't
   actually closed its target scenario (the liveness check only ran on the
   HTTP-failure path, which the two-instance case never takes, since the
   *other* instance's backend answers 200), and the fix itself introduced a
   new pid-reuse hazard (`try_wait()` reaps a dead child, and the new raw
   `libc::kill()` shutdown call had no guard against signaling an
   already-reaped, potentially-OS-recycled pid, unlike the `Child::kill()`
   it replaced). Per this process's "no second fix wave" rule, both were
   closed with one small, targeted, independently-verified follow-up
   rather than a third full review cycle — matching exactly how
   sub-project 1 closed its own equivalent residual finding.
3. **Score preview + playback UI. DONE — 9 of 9 tasks implemented and
   reviewed clean (Task 1 gated through a corrective exporter fix, 1b),
   final whole-branch review completed with one fix wave (a Critical
   duration-clamp defect in the exporter plus three Important frontend/doc
   findings), and the scoped re-review verified every finding addressed
   with all suites green (make test 153/153, vitest 77/77) and a real
   13-measure end-to-end transcription exercising the fixed paths.**
   Built the whole product/UI surface for
   this sub-project: a Home screen (upload via drag-drop or file picker,
   instrument choice, live transcription progress polling, a project list
   with retry-on-failure); a Score view (OSMD-rendered notation with a
   collapsible sidebar showing detection facts — key/tempo/meter with
   confidence dots — a TAB-staff visibility toggle for guitar, zoom
   controls, and MusicXML/MIDI export buttons); two interchangeable
   playback sources behind one `PlaybackSource` interface — the original
   recording (a real `<audio>` element) and a synthesized rendition (`smplr`
   WebAudio sampler over bundled soundfont samples) — switchable mid-
   playback in either direction with position preserved; and an OSMD-cursor
   sync layer that walks the cursor in step with whichever source is
   playing (chord = one step, rests filtered, TAB staff's duplicate
   per-staff notes deduped — rules discovered during Task 1's OSMD spike and
   consumed directly by Task 7). Task 1's spike also caught and fixed (via a
   corrective Task 1b) a real defect in `packages/musicxml` predating this
   sub-project: the exporter never emitted a TAB clef for guitar, ignored
   per-event onsets (collapsing piano's cross-hand rhythm), and never
   grouped same-onset events into `<chord/>` — all fixed before Task 1's
   OSMD-vs-Verovio gate was re-run and passed. Backend got 3 new/adjusted
   endpoints (project listing with job/export summaries, score JSON,
   normalized-audio streaming) plus origin-allowlisted CORS scoped to just
   `/v1/*` (both the dev Vite origin and the real `tauri://localhost`
   production origin — see Task 9's verification below for how the
   production half was empirically closed, not just source-verified). Spec:
   `docs/superpowers/specs/2026-08-17-score-preview-playback-ui-design.md`.
   Plan: `docs/superpowers/plans/2026-08-17-score-preview-playback-ui.md`.
   SDD workspace: `.superpowers/sdd/2026-08-17-score-preview-playback-ui/`.

   **Task 9's verification** (2026-08-18): a full guitar+piano journey
   (upload → live progress → score render → play the recording with the
   OSMD cursor visibly stepping in sync with `audio.currentTime` → toggle
   to the synth mid-playback and back, position preserved both directions,
   zero external network requests → export both formats) ran against the
   real `cargo tauri dev` process (real spawned backend, real webview)
   end-to-end, driven via Playwright against the Vite dev origin per this
   process's established "same bytes, same backend" precedent (Task 1,
   Task 8). `make test` is **151/151** across all 6 packages; the web suite
   is **74/74** (`vitest`) plus a clean `svelte-check`/`tsc` and `vite
   build`; `cargo build`/`cargo clippy` in `apps/desktop/src-tauri` are
   clean (Rust genuinely untouched by this frontend-only sub-project). A
   fresh `cargo tauri build` produced a real `.deb` bundling the new
   frontend, launched successfully under Xvfb from a `dpkg -x` extraction,
   and **closed a residual left open by Task 4**: Task 4 had source-verified
   (not observed) that the Linux production webview serves from
   `tauri://localhost`; Task 9 empirically confirmed both that the real
   packaged webview's origin genuinely is `tauri://localhost` and that a
   fetch from it to `http://127.0.0.1:8317/v1/projects` succeeds (`200 OK`,
   confirmed via a temporary on-page diagnostic reverted before commit, same
   discipline as Task 4's own probe).

   Two things worth knowing about, both documented in Task 9's report
   (`.superpowers/sdd/2026-08-17-score-preview-playback-ui/task-9-report.md`)
   rather than fixed there (neither is a regression from this sub-project's
   own tasks, and both are out of a verification task's scope to patch):
   1. **A real, intermittent WebKitGTK `fetch()` flake, newly discovered.**
      The real webview (dev *and* packaged) sometimes fails its first
      `fetch()` call to the local backend with a generic `TypeError: Load
      failed`, then either self-recovers on retry or doesn't for several
      retries — genuinely non-deterministic, not a simple cold-start race (a
      90s wait didn't reliably fix it). Ruled out as CORS, backend, or app-
      code related via multiple independent checks: `curl` with the exact
      `Origin` header always succeeds; a Playwright/Chromium session hitting
      the identical dev server never failed once across the whole journey;
      `strace -f -e trace=network` on both `WebKitNetworkProcess` and
      `WebKitWebProcess` showed **zero `connect()`/`socket()` syscalls** on a
      failing attempt (the failure happens before any network I/O, which
      also rules out a CORS-preflight rejection — that would still show a
      completed, blocked request). Most likely a quirk of this specific
      sandboxed container's `webkit2gtk-4.1` build, not a product defect;
      worth a real investigation in a future session if it recurs outside
      this environment. The existing Home screen's "Retry" button lets a
      user recover from it today.
   2. **Export-to-cwd, carried from Task 6, still real.** `<a download>` in
      the real WebKitGTK webview has no Rust `download-started` wiring, so
      exported MusicXML/MIDI files land in the Tauri process's cwd, not a
      user-chosen location. Needs `src-tauri` wiring to Tauri's
      `download-started`/save-dialog APIs — a good candidate for a small,
      bounded follow-up task before or alongside sub-project 4, since
      semantic editing will make exports something users reach for
      constantly, not just once per transcription.

4. **Semantic editing** — not started. See "Direction change" above for
   scope notes. Once sub-project 3's final whole-branch review completes,
   this is next — the edit-operation model (add/delete/move note, undo/redo,
   optimistic locking, locks) plus the UI to drive it, building on the Score
   view/OSMD/export foundation sub-project 3 just built.

**Update (final whole-branch fix wave, 2026-08-18): the paragraph
previously here — claiming `musicxml/export.py` appends notes in raw list
order and can scramble exported rhythm — was stale and wrong even at the
time it was written. Task 1b (referenced ~90 lines above) already fixed
onset ordering: `_events_to_notes_or_chords` groups every measure's events
by `Fraction(notatedOnset)` and iterates `sorted(groups)`, so placement is
always onset-ordered regardless of the input array's order (this is also
exactly why `apps/desktop/web/src/lib/timeline.ts` documents, at length,
that the JSON's raw event order is NOT onset-sorted — it only matters
there because the exporter itself never relies on it). No fix was needed
for that claim; it is retracted.

What the final whole-branch review actually found real in this file was a
different bug in the same neighborhood: `_insert_notated_events` placed
each element at its true (correctly onset-sorted) offset but never
clamped its duration, so two of `quantize.py`'s legitimate quantization
artifacts — a note's notated duration overlapping the next onset, or
running past the measure's own length — produced over-full measures and,
for bar-crossing notes, an extra tied-continuation note with no
corresponding onset group in the score JSON (this is what actually could
scramble/corrupt notation, and could blank the whole Score view via
`buildTimeline`'s count-mismatch guard). Fixed in this fix wave: every
element's effective duration is now capped to the room available before
the next onset (or the measure's end, for the last element), floored at
the quantization grid — see `_insert_notated_events`'s own docstring and
`_GRID_FLOOR_QL` in `packages/musicxml/src/musicxml/export.py`, and the
`test_score_json_to_musicxml_clamps_intra_measure_overlap` /
`test_score_json_to_musicxml_clamps_bar_crossing_duration` tests.

One genuinely remaining fidelity limit, pre-existing and NOT touched by
this fix (it lives in `quantize.py`, not `export.py`, and is out of this
fix wave's scope): `quantize.py`'s `measures` dict only ever gets an entry
for a measure number that has at least one note event in it — a measure
spanning pure silence (an inter-phrase rest with no onsets at all) never
appears in the score JSON's `measures` array, not even as an empty-events
entry. `_build_single_staff`/`_build_piano_grand_staff`/
`_build_guitar_notation_and_tab` all iterate `part_data["measures"]`
directly, so a silent measure is not rendered as a full-measure rest —
it's simply absent, and the exported bars end up packed contiguously with
non-consecutive `<measure number="…">` labels (e.g. 1, 3, 5) rather than a
true gap. Worth a small bounded fix on its own — have `quantize.py` emit
an explicit empty-events entry for every measure number in
`[1, last_measure_with_a_note]`, and have `export.py` render those as a
plain full-measure rest — before it's forgotten as "always been like
that."

Recommendation if picking up cold: read "Direction change" at the top of
this document first — it supersedes the framing below, and see "Offline
desktop app sub-projects" further down for current status. Sub-projects 1
and 2 (offline backend adaptation; desktop shell + packaging) are both done,
including their final whole-branch reviews. Sub-project 3 (score preview +
playback UI) has all 9 tasks implemented and reviewed clean (Task 1 gated
through a corrective exporter fix, 1b); its final whole-branch review has
NOT run yet — that's the immediate next step (same
`subagent-driven-development` process sub-projects 1 and 2 used), before
sub-project 3 can be marked done. Once that review (and any fix wave it
produces) completes, pick up sub-project 4 (semantic editing) next — no
spec exists yet for it, start with `superpowers:brainstorming`.

## Working process (established this session, keep using it)

This project uses the `superpowers` skill pack's workflow for every
non-trivial change:
`brainstorming` (classify scope, ask questions one at a time, propose
approaches, write a spec to `docs/superpowers/specs/`) →
`writing-plans` (turn the spec into a bite-sized TDD plan in
`docs/superpowers/plans/`, self-reviewed for spec coverage/placeholders/
type consistency) → **execution**, either `executing-plans` (inline, same
session) or `subagent-driven-development` (dispatch a fresh implementer +
reviewer subagent per task, with a scoped re-review after every fix round,
and a final whole-branch review on the most capable model before calling a
sub-project done). Both spec and plan get amended in place (not
rewritten) when reality contradicts them mid-implementation — see the two
worked examples below.

**Development happens directly on `main`** (explicit user choice — single-
owner project, no branch protection). No worktree. Push after every task
commit, not just at the end.

## Two examples of "verify before you write into the plan"

Both cost real time this session and are worth knowing about before
repeating the mistake:

1. **Meter detection scope.** The spec originally targeted 4 meters
   ({4/4, 3/4, 6/8, 2/4}). Empirical prototyping against synthetic click
   fixtures found `librosa.beat.beat_track` locks onto the *finest audible
   pulse*, not a stable tactus — this defeats simple-vs-compound (6/8)
   classification, and 2/4 turned out indistinguishable from 4/4 by the
   same technique. Scope was narrowed to {4/4, 3/4} *before* writing the
   plan, with the validated algorithm (accent-periodicity scoring on
   `beat_track`'s own beat times) written into both spec and plan.
2. **`music21` key detection.** The spec assumed `Stream.analyze('key')`
   defaults to Krumhansl-Schmuckler (commonly believed). It doesn't — it
   misclassified a clean C-major scale as A minor. Caught mid-
   implementation by a subagent, independently re-verified by the
   controller before ruling, fixed to `analyze('krumhansl')` explicitly.

**Lesson for future sub-projects:** algorithmic/library claims worth
writing into a plan's "complete code" (per `writing-plans`' "No
Placeholders" rule) are worth 5 minutes of direct verification against the
real library first, especially DSP/ML library defaults. Don't assume
Stack-Overflow-common knowledge about a library's default behavior is
correct — check it.

## Environment gotchas (sandbox-specific, not project design)

- **No external services needed anymore.** Sub-project 1 (offline backend
  adaptation, done — see "Offline desktop app sub-projects" above) swapped
  Postgres → SQLite and S3/MinIO → a local filesystem `LocalStorageClient`,
  and replaced the Redis/RQ job queue with an in-process thread pool.
  `make test` and the API run standalone with the Docker daemon down and
  zero Postgres/Redis/MinIO — proven directly by that sub-project's task 9
  (full suite green, manual curl smoke test through a standalone
  `uvicorn` process). Older revisions of this doc described native
  `postgres`/`redis-server`/`moto[server]` setup steps for local dev/test;
  those are now historical and no longer needed.
- **Docker Hub image pulls are blocked** by this sandbox's egress policy
  (`production.cloudfront.docker.com` denied). `infra/docker-compose.yml`
  and both Dockerfiles now target the superseded cloud (Postgres/S3/Redis)
  architecture and are currently broken/unverified against this
  sub-project's changes (e.g. `workers/transcription/Dockerfile` still
  runs `rq worker`, though `rq` was removed from the worker's
  dependencies) — pending removal or rework, not fixed here since that was
  ruled out of scope for this sub-project. Irrelevant to local dev/test
  now that it needs no external services (see above).
- **`.envrc`** at repo root now holds only `DATABASE_URL`
  (`sqlite:///./data/aura.db`) and `AURA_DATA_DIR`. Bash tool shell state
  does not persist between tool calls in this harness — every
  test-running command needs `source .envrc &&` prefixed, and use
  absolute paths for pytest file arguments (`uv run --package X` resolves
  relative paths against the invoked package's own directory in a way
  that's easy to get wrong).
- `setuptools<81` is pinned in `workers/transcription/pyproject.toml` —
  newer setuptools dropped `pkg_resources`, which `basic-pitch`'s
  `librosa`/`numba`/`resampy` chain still imports at runtime.
- `numpy<2` is pinned in `workers/transcription/pyproject.toml` — the
  `tensorflow` version pulled in by `basic-pitch` is ABI-incompatible with
  numpy 2.x; found and fixed during sub-project 1's e2e test migration
  (task 8).
- **`cargo tauri dev` must be invoked from the repo root with an explicit
  `--config` path**, not from `apps/desktop`:
  `cd /home/user/AuraAudio && xvfb-run -a cargo tauri dev --config
  apps/desktop/src-tauri/tauri.conf.json`. Invoking it from inside
  `apps/desktop` hits an ENOENT path-doubling bug in this tauri-cli version
  (found during sub-project 3, task 1). Same pattern applies to
  `cargo tauri build`.
- **Svelte 5 + `svelte-check@4.7.3` + `typescript@6.0.2`** (this project's
  pinned versions): a nullable `$state()` needs an **explicit generic**, not
  just a `let` type annotation — `$state<Foo | null>(null)`, not
  `let x: Foo | null = $state(null)`. With the annotation-only form,
  svelte-check infers the reactive type as the literal `null` and silently
  ignores the `let` annotation, surfacing only as a confusing "Property 'x'
  does not exist on type 'never'" wherever the value is later narrowed or
  accessed. Confirmed via a minimal repro during sub-project 3 (tasks 6-8).
- **`smplr@1.0.0`'s `Sampler({buffers})` has two real upstream bugs**,
  both worked around (not patched upstream) and both regression-tested —
  see `apps/desktop/web/src/lib/synth.ts`'s doc comments for the full
  traces: (1) `samplerToPreset` computes pitch/keyRange from a
  MIDI-sorted view internally but zips the result positionally against the
  *original* (alphabetical-by-filename) buffers-map key order, corrupting
  pitch mapping unless the buffers map is pre-sorted by MIDI number before
  construction; (2) omitting `decayTime`/`lpfCutoffHz`/`detune` from the
  `Sampler()` options (rather than passing them as explicit finite numbers)
  lets an internal `Object.assign`-style merge overwrite the library's own
  defaults with `undefined`, making every note's computed `detune` become
  `NaN` and throw inside `new Voice()`.
- **The bundled synth soundfont assets are ~22MB** (`tonejs-instrument-*`
  MP3 samples for piano + guitar, MIT-licensed), well above a typical "few
  MB" web-asset budget — a deliberate sub-project 3 (task 8) tradeoff:
  full-chromatic sample quality over size, justified against a desktop app
  whose backend PyInstaller bundle is already ~1.6GB. Revisit with a
  sparser, pitch-shifted sample set if package size ever becomes a real
  constraint.
- **Exported files (MusicXML/MIDI) land in the Tauri process's cwd, not a
  user-chosen location.** The Score view's export buttons use a plain
  `<a download>`, which WebKitGTK honors but with no native save dialog —
  Tauri's Rust side would need `download-started` event wiring plus a save-
  dialog API call to fix this properly. Known and accepted since sub-project
  3's task 6, re-confirmed still true by task 9's real-app verification.
  Worth a small bounded follow-up task, ideally before sub-project 4 makes
  exports something users reach for constantly.
- **After changing anything under `apps/api`'s routes** (the backend the
  desktop app bundles), the PyInstaller `aura-backend` bundle
  (`apps/desktop/dist/aura-backend/`, staged into
  `apps/desktop/src-tauri/resources/aura-backend/`) goes stale and must be
  rebuilt via `apps/desktop/build-backend.sh` before `cargo tauri
  build`/`dev` will reflect the change — the bundle is not automatically
  regenerated by `cargo build`/`tauri dev`, only re-staged/copied from
  wherever it already is. Sub-project 3 didn't touch backend routes after
  task 2-4 landed, so this wasn't hit again, but it's an easy trap for
  sub-project 4 (semantic editing), which will add real new endpoints.
- **Playwright** (kept as a deliberate `apps/desktop/web` devDependency,
  originally for sub-project 3 task 1's OSMD spike verification, reused
  through task 9's full journey) needs an explicit `executablePath` —
  `/opt/pw-browsers/chromium-1194/chrome-linux/chrome` — because the plain
  `/opt/pw-browsers/chromium` symlink in this sandbox points at a revision
  the installed `playwright` package doesn't expect.
- **A real, intermittent WebKitGTK `fetch()` "Load failed" flake**, newly
  discovered during sub-project 3's task 9 (see that task's report for the
  full diagnostic trail: `strace` showing no `connect()`/`socket()` syscall
  at all on a failing attempt, ruling out CORS/backend/app-code causes).
  Affects the real webview (both `cargo tauri dev` and the packaged
  `.deb`), not Playwright/Chromium sessions against the same dev server —
  purely an artifact of this sandboxed container's `webkit2gtk-4.1` build,
  most likely. Self-recovers on retry in some runs, doesn't in others; the
  Home screen's existing "Retry" button is today's only mitigation. Worth
  a real look if it recurs in a future session.

## Quick start for a fresh session

```bash
cd /home/user/AuraAudio
source .envrc && make test   # expect all six packages green (151/151 as of sub-project 3's task 9;
                              # apps/desktop's suite joined the other five during sub-project 2)
```

No external services to start first — see "Environment gotchas" above.
