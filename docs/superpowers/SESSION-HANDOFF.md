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
   2. **Export-to-cwd — RESOLVED after sub-project 4** by a bounded
      follow-up task: exports now open a native save dialog via the Tauri
      v2 `dialog` + `fs` plugins (`saveExport.ts`; minimal capabilities —
      `dialog:allow-save` + `fs:allow-write-file`, with the dialog plugin
      extending the fs scope to the user-picked path at runtime). Plain
      `<a download>` remains only as the non-Tauri dev-browser fallback.

4. **Semantic editing. DONE — 8 of 8 planned tasks plus one corrective
   task (7b) implemented and reviewed clean, final whole-branch review
   completed with one fix wave (4 Important findings — wrong-typed op
   payloads surfacing as 500s, a validation error rendered as the
   rederive-failure banner, History buttons dead after app restart, and
   one load-bearing stale comment — plus 4 promoted minors, all fixed),
   and the scoped re-review verified every finding addressed with all
   suites green (score_schema 119, worker 61, api 48, web 126).**
   Spec: `docs/superpowers/specs/2026-08-18-semantic-editing-design.md`.
   Plan: `docs/superpowers/plans/2026-08-18-semantic-editing.md`. SDD
   workspace: `.superpowers/sdd/2026-08-18-semantic-editing/`.

   Built: a semantic edit-operation model over the score JSON
   (`set_pitch`/`move_note`/`set_duration`/`delete_note`/`add_note`/
   `set_fingering`/`set_hand`/`set_locked`/`set_part_fact`, validated
   against `packages/score_schema/src/score_schema/edits.py`'s whitelist —
   Task 1); per-note lock flags the guitar string/fret and piano
   hand-assignment DP solvers honor when rederiving around a user's manual
   fingering/hand choice (Task 2); a `ScoreRevision` history per project
   with a `scoreHeadRevisionId` settings pointer, undo/redo/revert walking
   that chain (guarded against descending below the baseline revision),
   and a rederive-job coalescing/supersede rule for edits landing close
   together (Task 3); REST endpoints (`POST /v1/projects/{id}/edits`,
   `.../edits/undo`, `.../edits/redo`, `.../edits/revert`) returning the
   post-edit score plus a `rederive_job_id` (Task 4); a frontend `editor`
   Svelte store wrapping those endpoints with queued apply/undo/redo/
   revert, a generation guard against overlapping rederive polls, and
   (added by the 7b fix wave) a `reset()` for cross-project staleness
   (Task 5, closed by 7b); click-to-select note hit-testing over the
   OSMD-rendered SVG, correlating a click position back to a score event
   id (Task 6); and the Inspector UI — editable Detection facts (key/
   tempo/meter), a pitch/onset/duration stepper + lock toggle + delete for
   the selected note, an Add-note mini-form, Undo/Redo/Revert history
   controls, window-level keyboard shortcuts, and the post-edit refresh
   loop that re-fetches score JSON + MusicXML and re-renders notation once
   a rederive job settles (Task 7). Corrective Task 7b (dispatched after
   Task 7's live verification surfaced three cross-task defects, same
   pattern as sub-project 3's Task 1b) fixed: (A) `GET /v1/projects`
   listing a project's exports by its LATEST job's id, which broke
   permanently the first time any project was ever edited (every edit
   enqueues a new `TranscriptionJob` row with `stage="rederive"`, which
   immediately becomes "latest" — exports are now looked up by
   `project_id` directly); (B) the `editor` store singleton carrying stale
   `updating`/`canUndo`/`canRedo`/`error`/`selectedEventId` across a
   project switch (the hash router remounts `ScoreView` without a full
   page reload) — fixed with `editor.reset()`, called first in
   `ScoreView`'s `onMount`; (C) an unhandled promise rejection from
   `smplr`'s `Sampler` when a synth rebuild's `dispose()` lands while the
   previous instance's instrument buffers are still loading (guarded,
   swallowing only that specific disposal-race rejection).

   **Task 8's verification** (2026-08-18): rebuilt the bundled backend
   (`bash apps/desktop/build-backend.sh` — mandatory, since 7b changed
   `apps/api/routers/projects.py` after the last bundle build) and ran the
   full journey against the real `cargo tauri dev` process under Xvfb
   (fresh app-data dir, prior one backed up and restored after). A guitar
   project went through a complete edit session — pitch, move, add,
   delete, lock-a-note-then-edit-a-neighbor's-fingering-and-confirm-the-
   lock-survives-rederive, tempo, undo, redo, revert — plus a click-
   accuracy spot-check at 50% and 200% zoom (carried from Task 6's review)
   and a rapid-edit smoke test (7 pitch nudges fired synchronously, carried
   from 7b's Defect C) whose browser console showed zero errors — the
   disposal-race guard holds under real rapid editing, not just its unit
   tests. A dedicated, isolated check (run separately from the marathon
   session, after an unrelated test-driver mixup — see gotcha 7 below)
   confirmed exactly what Step 2 asks for: after one pitch edit, the Home
   project row still showed "Transcribed" and the export buttons still
   downloaded real files, and the downloaded MusicXML/MIDI on disk both
   reflected the edited pitch (MusicXML as its enharmonic spelling — e.g.
   an edit to A#2 exports as `<step>B</step><alter>-1</alter>
   <octave>2</octave>`, same MIDI note number either way — verified
   directly with `mido`). An abbreviated piano pass (hand override + undo)
   also ran clean. `make test` is **182/182** across all 6 packages; the
   web suite is **124/124** (`vitest`) plus a clean `svelte-check`/`tsc`
   and `vite build`; `cargo build`/`cargo clippy` in `apps/desktop/
   src-tauri` are clean (Rust genuinely untouched by this sub-project).
   Full journey log, screenshots, and suite output:
   `.superpowers/sdd/2026-08-18-semantic-editing/task-8-report.md`.

   Gotchas worth knowing for a future session:
   1. **OSMD's `Note.halfTone` is real MIDI pitch minus 12**, not the MIDI
      number it looks like — established in Task 6 (live-verified against
      exported MusicXML, not from the library's own misleading docstring)
      and reused as-is by every later task that reads a clicked note's
      pitch.
   2. **`cache: "no-store"` is required on every fetch of mutable-content
      URLs** (`GET /v1/projects/{id}/score`, `GET /v1/exports/{id}/
      download`) — both URLs are stable across a rederive (the worker
      rewrites the same DB row/file in place), so the browser's heuristic
      HTTP cache will silently serve pre-edit bytes without this. Task 7
      found and fixed this for the two `fetch()` calls `ScoreView.svelte`
      makes; Task 8 additionally confirmed (a three-way comparison — a
      fresh Node `http.get`, a page `fetch()` with `cache:"no-store"`, and
      a real click on the Sidebar's `<a download>` Export button, all
      after the same edit) that the export buttons themselves come back
      byte-identical and correctly edited every time.
   3. **Rederive jobs share the `TranscriptionJob` table** — every edit/
      undo/redo/revert enqueues a new row with `stage="rederive"`, which
      immediately becomes a project's "latest job" by `created_at`. Home's
      status chip therefore briefly flickers to reflect the rederive job's
      own transient state right after an edit — cosmetic only (spec §4.3
      mandates jobs-endpoint observability without a schema change; this
      was a deliberate, documented tradeoff, not missed).
   4. **Exports are listed by `project_id`, not by the latest job's id**
      (7b's Defect A fix) — `Export` rows keep the id of the ORIGINAL
      transcription job forever; the rederive worker rewrites their
      `object_key`/`status`/`revision` in place but never `job_id`. Any
      future change to `list_projects`'s export query should keep
      querying by `project_id` — reverting to "latest job's exports"
      reintroduces 7b's exact permanently-empty-exports bug.
   5. **The zero-backend-change "retry a failed rederive" trick**: replay
      `set_locked` on any existing event with its OWN current `locked`
      value — a semantically null edit (byte-identical resulting score)
      that still goes through `apply_project_edit`'s ordinary
      enqueue-a-rederive-job path, so a failed rederive can be retried
      without a dedicated endpoint (`ScoreView.svelte`'s `retryRederive`).
   6. **`editor.reset()` must run first in `ScoreView`'s `onMount`**, before
      `loadScore()` — the hash router remounts `ScoreView` per project
      without a full page reload, and `editor` is a module-level singleton
      (7b's Defect B; see above).
   7. **Test-driver gotcha, not a product bug** (found during Task 8): when
      driving multiple projects with Playwright across a Home round-trip,
      navigate to a project by its explicit id (set `location.hash`
      directly) rather than clicking a row by list position/`.first()` —
      Home's project list can contain more than one row transcribed from
      the same source audio (e.g. a leftover exploration project next to
      the real test project), and `.first()` after a `back-link`
      navigation is not guaranteed to land on the row the test just
      edited. Confirmed by an isolated re-test with the identical wait
      pattern that the product itself has no export-staleness bug here —
      only that one test script's row-selection was ambiguous mid-session.
   8. **RESOLVED — head-pointer staleness on re-transcription**: was latent
      (no UI flow re-transcribes an existing project), now closed defensively
      regardless — `quantize.run` clears a project's stale
      `scoreHeadRevisionId` settings pointer (Task 3) whenever it writes a
      fresh rev-0 `ScoreRevision`, so a project would no longer keep serving
      an old, edited score after a hypothetical re-transcription. See
      `workers/transcription/src/aura_worker/stages/quantize.py` and its
      tests in `workers/transcription/tests/test_quantize.py`.
   9. **The transcribe->edit->undo->export journey is now automated** (a
      follow-up flagged at the top of this document, closed): a committed
      Playwright regression test, `apps/desktop/web/e2e/edit-journey.spec.ts`
      (`npm run test:e2e` / `make e2e-web`), drives the real UI against a
      really-spawned backend and a real Vite dev server — upload, real
      basic-pitch transcription, click-select a note on the rendered SVG,
      pitch edit, undo, redo, and an export-download assertion that mirrors
      the real `musicxml.export._spell_pitch` spelling function as an
      oracle rather than reimplementing it. No app source or testability
      hooks were added.

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

**RESOLVED (silent-measure fidelity fix, 2026-08-19)**: a measure spanning
pure silence (an inter-phrase rest with no note onsets at all) used to be
dropped entirely from the score JSON's `measures` array instead of kept as
an empty-events entry, which packed exported bars contiguously with
non-consecutive `<measure number="…">` labels (e.g. 1, 3, 5) rather than a
true gap. Fixed: `quantize.py` now emits an explicit empty-events entry for
every measure number in `[1, max(occupied measure number)]`
(`STAGE_VERSION` bumped for cache invalidation), `_insert_notated_events` in
`export.py` already rendered a zero-event measure as one whole-measure rest
per staff (pinned with new tests, no code change needed there), and
`_rebucket` in `score_schema/edits.py` got the matching fix so a meter
change no longer drops a measure that has gone silent (e.g. via
`delete_note` removing its only event). See
`packages/musicxml/tests/test_export.py`,
`workers/transcription/tests/test_quantize.py`, and
`packages/score_schema/tests/test_edits.py` for the pinning/regression
tests.

Recommendation if picking up cold: read "Direction change" at the top of
this document first — it supersedes the framing below, and see "Offline
desktop app sub-projects" further down for current status. All four
offline-desktop sub-projects (offline backend adaptation; desktop shell +
packaging; score preview + playback UI; semantic editing) are DONE,
including their final whole-branch reviews and fix waves, and merged to
main. The next piece of work has no spec yet — start with
`superpowers:brainstorming` on whatever the user directs (candidate
follow-ups are recorded in the sub-project sections above: an automated
Playwright edit-journey regression test, silent-measure fidelity in
quantize (RESOLVED 2026-08-19, see above), and re-transcription
head-pointer invalidation (RESOLVED 2026-08-19, see gotcha 8 above)).

## YouTube import (network exception to the offline principle)

Added: `POST /v1/imports/youtube` (`apps/api/src/aura_api/routers/imports.py`)
downloads a YouTube video's audio via `yt-dlp` and registers it through the
same `LocalStorageClient` path `POST /v1/uploads` uses, so the response is
shape-compatible (`object_key`, plus an optional best-effort `title`) and
Home's existing create-project flow (`chooseInstrument` in `Home.svelte`)
consumes either source uninformed of which one produced it. `GET
/v1/system/deps` gained a third `ytDlp: {found, version}` entry alongside
`ffmpeg`/`ffprobe`.

**This is the app's FIRST network-using feature.** Every other capability
(transcription, editing, export) is deliberately fully offline — sub-project
1 above ("Offline backend adaptation") exists specifically to guarantee
that. YouTube import is a scoped, user-approved exception to that
principle, not a reversal of it: it's the one optional path a user can
choose to take onto the network, and only when they explicitly paste a URL.

Design points worth knowing if extending this:

- **yt-dlp is optional-on-PATH, not bundled.** Same guided-install pattern
  as ffmpeg (`deps.ts`'s `INSTALL_COMMANDS`, now keyed by dependency name
  instead of assuming ffmpeg is the only one) — checked via `shutil.which`,
  with a per-OS one-line install command shown in the frontend when
  missing. Unlike ffmpeg it is **non-blocking**: `SystemDepsResponse.allFound`
  stays scoped to `ffmpeg`/`ffprobe` only, and yt-dlp missing never trips
  Home's existing transcription-blocking banner — only the YouTube-import
  affordance itself gates on it (`isYtDlpMissing` in `deps.ts`). yt-dlp is
  also deliberately **not** added to the desktop app's deb `Depends`
  (`apps/desktop/src-tauri/tauri.conf.json` still lists only `["ffmpeg"]`).
- **Why not bundled: yt-dlp churns.** YouTube changes its player/delivery
  internals often enough that yt-dlp ships frequent releases just to keep
  working. A bundled, pinned copy would go stale between app releases in a
  way ffmpeg (a stable, slow-moving dependency) doesn't. Relying on the
  user's system package manager (`winget`/`brew`/`apt`) means yt-dlp stays
  current automatically instead of the app needing its own update channel
  for it.
- **ToS note.** Downloading audio from YouTube may violate YouTube's Terms
  of Service depending on jurisdiction and use. This is the user's
  responsibility, not something the app enforces or adjudicates — the
  feature is intended for content the user owns or is licensed to use
  (e.g. their own uploads, content they have rights to transcribe). No
  consent dialog or disclaimer was added; if that changes, it belongs in
  the YouTube-import panel on Home, not buried in a settings screen.
- **mp3, not the source codec.** The download always transcodes to mp3
  (`-x --audio-format mp3`) because the transcription worker's probe step
  only accepts `{"pcm_s16le", "mp3", "aac", "h264"}`
  (`workers/transcription/src/aura_worker/ffmpeg_utils.py`'s
  `_ALLOWED_CODECS`) — YouTube's native delivery codecs (webm/opus) would
  fail probe otherwise.
- **argv list, 300s timeout, 200m cap, temp dir cleaned up.** yt-dlp always
  runs as a list (`subprocess.run([...], ...)`, never `shell=True` or
  string interpolation) into a temp dir under `AURA_DATA_DIR/imports_tmp`,
  deleted in a `finally` block regardless of outcome.
- **URL validation checks the PARSED hostname**, not a substring of the raw
  URL, on both sides (`_ALLOWED_HOSTS` in `imports.py`, mirrored in
  `lib/youtube.ts`'s `isYoutubeUrl`) — specifically to reject the userinfo
  trick `https://youtube.com@evil.com/...`, whose real (parsed) hostname is
  `evil.com`.

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
- **Exports use a native save dialog (RESOLVED).** Originally exports
  landed in the Tauri process's cwd via plain `<a download>`; a bounded
  follow-up after sub-project 4 added the Tauri v2 `dialog` + `fs`
  plugins and `apps/desktop/web/src/lib/saveExport.ts` (save dialog with
  suggested filename, per-button Saved/error feedback, cancel = no-op;
  `<a download>` kept as the non-Tauri dev-browser fallback). Capabilities
  are minimal — `dialog:allow-save` + `fs:allow-write-file`; the dialog
  plugin extends the fs scope to the picked path at runtime, so no static
  directory scope exists.
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

## Detection-quality roadmap (user-approved, in progress)

Approved order — each item is reviewed + benchmark-gated before the
next; 3 and 4 proceed only if 0-2 land OK:

0. **Benchmark harness (prerequisite).** Synthesized fixtures with
   known reference events + optional local real-recording manifest;
   note onset F1 / onset+offset F1 (mir_eval), key/meter/tempo
   accuracy; baseline committed under docs/benchmarks/. No tuning
   without measurement.
1. **Post-filter cheap wins. DONE (2026-08-21), benchmark-verified.**
   Ghost-note filtering (confidence floor + min-duration floor +
   octave-shadow dedupe — `aura_worker.ghost_filter`) and per-instrument
   basic-pitch onset/frame threshold tuning (`aura_worker.instrument_thresholds`)
   both landed in the `inference` stage right after basic-pitch's raw
   predictions (not in `quantize` as originally sketched above — see
   deviation note below), `STAGE_VERSION` 1 → 2. Diagnosis (dumping
   matched/unmatched notes for the worst fixtures) found the baseline's
   0.540 mean onset F1 was driven almost entirely by poor precision
   (0.24-0.59 per fixture) against near-perfect recall (0.75-1.0) — i.e.
   the loss was dominated by ghost/extra notes, not missed real ones,
   which is exactly the shape this item's filter targets rather than
   raising thresholds blindly (which would have cut recall instead).
   Result: **mean onset F1 0.540 → 0.971 (+0.431)**, onset+offset F1 0.155
   → 0.256, tempo/key/meter accuracy unchanged (100%/50%/20%), every one
   of the 10 fixtures improved (worst per-fixture delta +0.226, nowhere
   near a regression). Full before/after table:
   `docs/benchmarks/2026-08-21-dq1.md`. `test_benchmark_regression.py`'s
   `FLOOR` raised 0.45 → 0.83 to match the new measured 3-fixture subset
   mean (0.9899 - 0.15 margin).
   Deviation from the sketch above: the ghost-note filter lives in
   `inference.py` (applied to basic-pitch's raw output), not `quantize.py`
   — the benchmark harness scores `inference.run`'s raw notes directly
   (by design, see DQ-0's report), so a filter placed in `quantize` would
   never show up in the very benchmark this item is gated on; `inference`
   is also the natural place architecturally, since ghost notes are an
   inference-stage artifact and `quantize`/`structure` both already
   consume the filtered list downstream.

   **Post-commit code review (`6f15b8e`) verdict: with fixes, now
   addressed — see `docs/benchmarks/2026-08-21-dq1b.md`.** The
   improvement itself was independently reproduced bit-for-bit (0.971) and
   traced end-to-end. Three follow-ups, all closed same-session:
   - **Fast-note deletion risk — CLOSED, not just documented.** The
     reviewer's concern: `MIN_DURATION_S=0.15` could silently delete
     genuine 16th notes (0.125s @120bpm, 0.083s @180bpm), untested because
     the original suite's fastest case was eighth notes @130bpm (0.196s).
     Fix: two 16th-note-run fixtures added to `test_fixtures.benchmark_suite`
     (`BENCHMARK_SUITE_VERSION` → `2026-08-21-v2`, nominal note length
     ~0.091s), then re-measured against basic-pitch's REAL raw output at
     production thresholds. Result: the smallest true-positive raw
     duration measured is 0.1858s and the largest ghost duration below the
     floor is 0.1393s — `MIN_DURATION_S=0.15` sits cleanly between them
     and is CONFIRMED, not moved. The actual fast-passage recall loss that
     remains (guitar 0.857, piano 0.476 onset F1) is basic-pitch's own
     onset-merging behavior, not this filter — proven directly by a new
     regression test
     (`test_fast_passage_regression.py::test_ghost_filter_duration_floor_is_not_the_bottleneck_on_a_fast_passage`,
     asserts filtering never reduces onset F1 below the unfiltered value
     on the fast fixture). A tempting further fix (lower piano's
     `frame_threshold` from 0.1 to 0.2, which helps the fast fixture) was
     evaluated and deliberately NOT taken — it would regress the original
     4 piano fixtures beyond DQ-1's own 0.05-drop gate (e.g.
     `piano_melody_c_major_100` 1.000 → ~0.94), so it's recorded as a
     disclosed trade-off in `aura_worker.instrument_thresholds`'s
     docstring instead of silently applied. The original 10 fixtures are
     bit-for-bit unchanged by any of this (zero delta, see dq1b.md);
     `test_benchmark_regression.py`'s 3-fixture subset and `FLOOR` (0.83)
     are unaffected since none of those 3 fixtures were touched.
   - **Grid-search evidence now committed and rerunnable.** The "confirmed
     by a finer grid, not sweep noise" claim (piano `frame_threshold=0.1`)
     previously cited a table that didn't exist in git. Fixed:
     `workers/transcription/scripts/tune_instrument_thresholds.py` (a
     real, rerunnable script) plus its last output,
     `docs/benchmarks/2026-08-21-threshold-sweep.md`, are both committed —
     every number in `aura_worker.instrument_thresholds`'s docstring now
     traces to that file.
   - **Methodology caveat and octave-dedupe risk, now stated explicitly**
     (in both `aura_worker.ghost_filter`'s and
     `aura_worker.instrument_thresholds`'s docstrings, not just here):
     every constant in this item — confidence floor, duration floor,
     octave-shadow ratio, both instruments' onset/frame thresholds — is
     tuned AND gated on the same synthetic benchmark suite it's measured
     against. There is no held-out fixture set, and no real-recording
     manifest run (`--manifest`, see DQ-0's harness) has validated any of
     it yet — these are honest, suite-relative optima, not a guarantee of
     real-world behavior. Separately, the octave-shadow dedupe
     (`OCTAVE_CONFIDENCE_RATIO=0.75`) has a real, unmitigated blind spot:
     basic-pitch's `confidence` (mean frame activation) cannot in
     principle distinguish "a harmonic overtone mis-detected as its own
     note" from "a real note played much more softly an octave away from
     a louder one" — both look identical to this heuristic, and the ratio
     itself was tuned against exactly one observed example, not a
     distribution. No suite fixture currently exercises a genuine soft
     octave-doubled note, so this risk is real but not benchmark-covered.
     Mitigation is real but partial: a wrongly-deleted note is
     recoverable through the desktop app's editor (add-note flow, see
     sub-project 4 above) rather than silently, permanently lost — but
     that mitigation requires the user to notice and doesn't reduce the
     odds of the deletion happening in the first place.
2. **Piano-specific model. SHIPPED (2026-08-21), with a disclosed
   benchmark-gate caveat — reviewer input wanted before treating this as
   fully closed.** ByteDance/Kong's piano transcription CRNN
   (`piano_transcription_inference`, PyPI, torch-based) now runs piano
   projects behind the existing engine adapter
   (`aura_worker/piano_engine.py`, routed from
   `aura_worker/stages/inference.py`, `STAGE_VERSION` 2→3); guitar is
   untouched byte-for-byte (verified: all 7 guitar benchmark fixtures
   score field-by-field identical to dq1b). Full candidate assessment,
   `uv lock` resolution evidence, license record, threshold re-derivation,
   and benchmark are in `docs/benchmarks/2026-08-21-dq2.md` — read that
   file before touching this item further, this paragraph only summarizes
   it.

   **Offline + license, both clean.** The checkpoint (~164MB,
   CC-BY-4.0 — Zenodo record 4034264's own API metadata) is fetched at
   BUILD time only (`workers/transcription/scripts/
   fetch_piano_weights.py`, sha256-pinned, idempotent) into a gitignored
   `workers/transcription/weights/piano/` — never vendored into git (no
   git-lfs in this repo, and 164MB would be an unprecedented single-file
   commit) and never downloaded at transcription-request time
   (`piano_transcription_inference`'s own checkpoint-loading code skips
   its built-in Zenodo download whenever a local, correctly-sized
   `checkpoint_path` already exists — verified directly against its
   `inference.py`). `apps/desktop/build-backend.sh` now runs the fetch
   script and stages the checkpoint into the PyInstaller bundle via
   `--add-data ...:piano_weights`, mirroring how basic-pitch's own
   weights are already collected via `--collect-data basic_pitch`.

   **Dependency resolution: clean, one disclosed exception.** `torch` is
   sourced from `download.pytorch.org/whl/cpu` (CPU-only wheels,
   `sys_platform`-scoped to linux/win32; macOS's stock PyPI wheel already
   has no CUDA tax) specifically because plain PyPI `torch` on Linux
   unconditionally pulls a multi-GB CUDA stack via its own
   `install_requires` — verified directly against PyPI's `requires_dist`,
   not assumed. A real `uv lock` (scratch copy first, then for real) left
   every pre-existing pin byte-identical (`tensorflow==2.14.0`,
   `numpy==1.26.4`, `tensorflow-intel==2.14.0` on win32, `basic-pitch`,
   `setuptools<81`, etc.) except one disclosed, minor exception: `sympy`
   1.14.0→1.13.1 (a pre-existing transitive dependency of `coremltools`,
   a `basic-pitch[tf]` extra never imported by this app's runtime code —
   `torch<2.7`'s own pin narrowed sympy's allowed range).

   **Installer size: measured, not guessed** (PyPI file listings + real
   `uv lock` + range-request probes against the actual CDN `uv lock`
   resolved to). Estimated on-disk bundle growth: **Linux ~+353MB
   (684MB→~1.03GB), Windows ~+384MB (419MB→~803MB), macOS ~+239MB
   (460MB→~699MB)** — real, non-trivial growth (~50-90%), but nowhere
   near the multi-GB explosion the CUDA-bundled default `torch` wheel
   would have caused (900MB+ for the wheel alone, before several more GB
   of forced nvidia packages).

   **The benchmark caveat, stated plainly:** on the exact synthetic
   12-fixture suite this item's own hard constraint names, piano-cohort
   mean onset F1 **regresses** (dq1b's 0.855 → 0.758 on the same 5
   fixtures, unchanged specs). This is disclosed, not hidden or argued
   away. Direct, controlled A/B evidence (added as 2 new committed
   fixtures, `test_fixtures.real_piano`, `BENCHMARK_SUITE_VERSION`
   2026-08-21-v2→v3 — real per-semitone piano recordings, same
   MIT-licensed samples already vendored for the desktop app's synth
   playback, re-rendering 2 of the existing piano specs' EXACT note lists
   with real audio instead of the synthetic decaying-harmonic "tone"
   timbre) found the regression is a fixture-timbre artifact, not a real
   capability regression: on real piano audio, the new engine wins
   decisively over basic-pitch (0.875→1.000 monophonic melody,
   **0.629→0.980 polyphonic two-hand chords — basic-pitch's recall
   collapses to 0.458 on real piano chords, the new engine reaches
   1.000**). basic-pitch's synthetic-suite scores benefit from being
   grid-tuned against that exact synthetic waveform in DQ-1, an advantage
   this investigation's quick 5-fixture threshold sweep for the new
   engine did not have time to match. The ship call was made anyway,
   reasoning that real-audio evidence is the more faithful predictor of
   production quality (this app never transcribes synthetic sine-ish
   audio) — but this is a judgment call a human reviewer should weigh in
   on, not a clean pass. **If a stricter reading of the literal gate is
   wanted:** reverting is a small, single-purpose change — set
   `STAGE_VERSION` back to 2 and remove the piano branch in
   `inference.run()` — everything else (dependencies, weights fetch,
   `piano_engine.py`, the new fixtures) can stay in place inert pending a
   properly weighted suite update.

   **Adjudication (2026-08-21): SHIP, with fixes — both judgment calls
   above ruled in favor of shipping, independently re-verified** (the
   real-piano fixtures confirmed genuinely MP3-sample-based, not a
   relabeled synthetic render; basic-pitch's 0.458 recall collapse on
   real polyphonic piano chords reproduced to 3 decimals; the `sympy`
   pin move ruled acceptable — `music21` has zero `sympy` dependency, and
   only the darwin-gated `coremltools` extra plus `torch` itself depend
   on it). Four fixes required before this counted as done, all applied
   same-session:
   1. **CI never fetched the checkpoint** — `apps/api/tests/
      test_e2e_pipeline.py`'s piano case and several `workers/
      transcription/tests` (the benchmark-regression floor guard, the
      fast-passage regression) now need it, but `.github/workflows/
      ci.yml`'s `python` job never ran `fetch_piano_weights.py`. This was
      caught by the adjudication reproducing a real failure (pointing
      `AURA_PIANO_CHECKPOINT_PATH` at nothing) — the original 525/525
      green claim was true only because it rode on this sandbox's already-
      cached weights, not because CI would actually pass on a fresh
      checkout. Fixed: `ci.yml`'s `python` job now runs `fetch_piano_
      weights.py` (checksum-verified, same script `build-backend.sh`
      uses) before any test step, cached via `actions/cache` keyed on the
      fetch script's own contents (so a checksum/URL change
      auto-invalidates the cache). Verified for real in this sandbox by
      relocating `workers/transcription/weights/` (simulating a fresh
      checkout), confirming the exact tests the adjudication predicted
      would fail actually did (`test_benchmark_regression.py`,
      `test_fast_passage_regression.py`'s piano case), then running the
      fetch step and confirming green again (4/4), then the full 525/525
      suite green once more.
   2. **CC-BY-4.0 attribution didn't reach end users.** Dev docs
      (`dq2.md`) aren't enough for a license that requires attribution on
      redistribution — added `THIRD_PARTY_NOTICES.md` at repo root
      (checkpoint CC-BY-4.0 citation/DOI, the tonejs piano-sample MIT
      notice, basic-pitch's Apache-2.0 note, a pointer to TensorFlow's own
      bundled notices file), staged into every packaged installer by
      `build-backend.sh` (`--add-data THIRD_PARTY_NOTICES.md:piano_weights`
      — lands right next to the checkpoint it documents). An in-app
      notices screen remains a tracked follow-up, not done here (see
      below).
   3. Refreshed two stale docstrings/comments that predated the piano
      engine but still described basic-pitch-only behavior:
      `test_fast_passage_regression.py`'s module docstring (piano now
      runs through `aura_worker.piano_engine`, not basic-pitch — the
      piano floor was also re-measured against the new engine's real
      score, 0.35→0.55, since the old 0.35 floor was derived from
      basic-pitch's 0.476, not the new engine's 0.651) and `aura_worker.
      eval.benchmark`'s module docstring (now states the piano checkpoint
      must be fetched first, or every piano fixture fails loudly with
      `PianoWeightsMissingError`).
   4. `apps/desktop/aura-backend.spec` (PyInstaller-GENERATED, rewritten
      fresh by every `build-backend.sh` run) had been tracked in git by
      mistake and baked in a contributor's local absolute path. Gitignored
      and removed from tracking; verified `build-backend.sh` still
      regenerates it correctly on every run (unaffected — it never reads
      the tracked copy).

   **Follow-up items tracked open (not done, adjudication explicitly
   flagged these for a future session):**
   - The committed benchmark suite's piano cohort should move toward
     real-piano-timbre fixtures as its primary measure — the 2 added here
     are a proof of concept demonstrating the gap, not a full replacement.
     Until that's done, treat the synthetic suite's piano numbers as an
     incomplete signal for any future piano-detection-quality work.
   - Ghost-filter / octave-shadow analysis for the new piano engine's own
     confidence distribution has NOT been done — the filter is bypassed
     entirely (see below), which is the right call given it exposes no
     per-note confidence signal today, but if a future change adds one
     (e.g. sampling the model's raw onset activation instead of using the
     velocity proxy), this filter question should be revisited from
     scratch against that new signal's own distribution, not by reusing
     basic-pitch's tuned constants.
   - An in-app "Third-Party Notices" screen (surfacing
     `THIRD_PARTY_NOTICES.md`'s content inside the app itself) is pending
     — the file ships on disk in every installer (see fix 2 above), but
     nothing in the UI points a user at it yet.

   Full detail, all numbers, and the license record:
   `docs/benchmarks/2026-08-21-dq2.md`. Ghost-note filtering
   (`aura_worker.ghost_filter`) is deliberately NOT applied to this
   engine's output — it exposes no per-note confidence signal the filter
   could use; see that file's "Ghost filter" section for the full
   rationale.
3. **Source separation. SHIPPED (2026-08-21), opt-in, guitar only —
   reviewed with fixes (2026-08-22), all three closed.** Optional Demucs
   (`demucs` PyPI, `htdemucs_6s` weights, MIT-licensed code) "isolate
   instrument from mix" toggle before inference — a new `separate` worker
   stage between `probe` and `normalize`, gated on
   `Project.settings["separateSource"]` (existing JSON column, no DB
   migration) AND `instrument == "guitar"`, wired from
   `aura_worker.runner.run_transcription_job`. A Home-screen checkbox
   ("Isolate instrument from mix (Guitar only)") at project creation
   (`apps/desktop/web/src/components/Home.svelte`) sets it via
   `POST /v1/projects`'s new `separate_source` field (default `false`,
   never opt-out-by-default). `aura_api.hashing.compute_input_hash` now
   folds the flag into `input_hash`, so toggling it re-transcribes instead
   of reusing a cached job/artifact — verified by a real committed test
   (`apps/api/tests/test_idempotency.py`). Full candidate assessment,
   `uv lock` evidence, license record, stem-mapping investigation (two
   qualitative rounds — a first "target the `other` stem alone" pass
   looked like a clean win on one fixture and then caused a **total
   transcription failure, 0 detected notes**, on a second, real, committed
   benchmark fixture; fixed by summing the `guitar` and `other` stems),
   and CPU timing are in `docs/benchmarks/2026-08-21-dq3.md` — read that
   file before touching this item further, this paragraph only summarizes
   it. **Piano is a documented no-op, not shipped**: `htdemucs_6s`'s own
   piano stem is unreliable (matches upstream's own "doesn't work so well"
   caveat) — a piano project with the setting enabled is inert by design,
   verified directly in dq3.md's own mixed-fixture table (OFF and ON rows
   byte-identical for the piano fixture, reproduced across all 4 benchmark
   runs).

   **Review verdict on the first version (commit `81d5299`): with fixes —
   engineering upheld (piano-skip verified true-skip, hash/stage/CI all
   confirmed, bundle checksum re-verified), three hygiene items, all
   closed same-session:**
   1. **Benchmark determinism (the real finding) — FIXED.** An independent
      from-scratch rerun did not reproduce 3 of 10 non-piano deltas the
      first version reported (the exact "worst case" number its Ship
      decision leaned on), traced to `demucs.apply.apply_model`'s own
      `shifts` parameter defaulting to `1`, not `0` — a genuinely RANDOM
      time-shift test-time-augmentation trick, not floating-point noise
      (isolated proof: two back-to-back calls on the IDENTICAL decoded
      input tensor differed by 14% of the stem's own peak amplitude).
      Thread-pinning alone did NOT fix it, confirming the random shift —
      not parallel-reduction ordering — was the actual cause. Fixed:
      `aura_worker.separation.separate_guitar` now passes `shifts=0`
      explicitly. Re-verified with **4 separate consecutive runs** of
      `dq3_mixed_benchmark.py` after the fix — every one of the 10
      mixed/clean onset-F1 deltas was bit-identical across all 4 (full
      determinism, not just a tightened variance band, so no mean±range
      reporting was needed). A regression test
      (`test_separate_guitar_is_deterministic_across_calls`) pins two
      independent calls producing byte-identical output. dq3.md and this
      entry's numbers are now the corrected, reproducible values — the
      real worst-case clean-fixture delta is **-0.222**
      (`guitar_melody_g_major_120_3_4`), not the smaller, non-reproducible
      -0.176 the first version reported for a different fixture. CPU
      timing was re-measured honestly after the fix too: it got FASTER
      (removing the random-shift augmentation removed an extra forward
      pass), not slower — see below.
   2. **License acceptance record — CLOSED.** The product owner accepted
      the demucs weights-license risk (MUSDB18-HQ's non-commercial
      training-data license reaching the pretrained weights, no separate
      weight license from Meta — see dq3.md's "License record") on
      **2026-08-22, for this app's personal-use context**, after the
      disclosure was surfaced. Recorded in `docs/benchmarks/
      2026-08-21-dq3.md`'s "License record" and "Ship decision" sections.
      This closes the "a human reviewer should independently weigh in"
      item as a decision, not an open question — the small, localized
      revert path (a single `if` gate in `aura_worker.runner.
      run_transcription_job`) remains documented in dq3.md if a future
      context ever needs a stricter reading.
   3. **Checkbox UX (silent no-op for Piano) — FIXED.** The toggle
      rendered before the instrument was chosen and gave no feedback when
      a user picked Piano with it checked. Fixed: the checkbox label now
      reads "Isolate instrument from mix (Guitar only)"; a new
      `apps/desktop/web/src/lib/separation.ts` (`separationAppliesToInstrument`,
      `separationNoopNote`) is the single source of truth for "does this
      apply to instrument X", mirroring `aura_worker.runner`'s own gate;
      Home.svelte shows a visible, accent-colored inline note ("Won't
      apply to Piano — Isolate instrument from mix only works for Guitar
      right now") whenever the box is checked, and the same text appears
      as the Piano button's `title` tooltip — non-silent both before and
      at the moment of clicking Piano, not just after. Covered by a new
      `apps/desktop/web/src/lib/separation.test.ts` (5 cases).

   **CPU time** (re-measured post-fix, averaged over the same 4
   determinism-verification runs, since the F1 scores are deterministic
   but wall-clock timing naturally varies with OS/CPU scheduling): mean
   12.6s for a 30s clip (range 11.2–13.5s), mean 21.3s for a 60s clip
   (range 20.9–22.2s) — both FASTER than the pre-fix version reported
   (removing the random-shift augmentation removed an extra forward
   pass). Linear fit extrapolates to **≈1.5 minutes of extra compute for
   a 5-minute song**, comfortably under the 10-minutes-extra UX threshold
   this item's own hard constraints named; the `separate` stage shows in
   the Home screen's existing per-stage progress label so the wait isn't
   silent.

   **Verification performed** (post-fix, final numbers): `workers/
   transcription` 163/163 (153 pre-existing + 10 new: `test_separation.py`'s
   7 + `test_separate_stage.py`'s 3 — one determinism regression test
   added in the fix wave), `aura-api` 82/82 (78 pre-existing + 4 new: 2
   `test_idempotency.py` toggle tests + 2 `test_projects.py` setting
   tests), `score_schema` 173/173, `musicxml` 47/47, `aura-api` desktop
   tests 10/10, `test_fixtures` 76/76 (64 pre-existing + 12 new —
   `test_mixed.py`'s 6 + `test_mixed_benchmark.py`'s 6). Total across all
   Python packages: 551/551. Frontend: `vitest` 195/195 (190 + 5 new in
   `separation.test.ts`), `svelte-check`/`tsc` clean (513 files, 0
   errors/warnings). CI (`.github/workflows/ci.yml`) gained a
   demucs-weights cache/fetch step mirroring the piano checkpoint's own
   pattern, keyed on `fetch_demucs_weights.py`'s own contents.
   `apps/desktop/build-backend.sh` now fetches the demucs weights and
   stages them (`.th` + `.yaml` manifest + `THIRD_PARTY_NOTICES.md`) into
   the PyInstaller bundle at `demucs_weights/`, mirroring the piano
   checkpoint's staging — ran for real, locally
   (`bash apps/desktop/build-backend.sh`), and confirmed directly
   (`find`/`du`/checksum, not just "the script exited 0") that the built
   bundle actually contains `demucs_weights/5c90dfd2-34c22ccb.th` (correct
   size), `demucs_weights/htdemucs_6s.yaml`, and
   `demucs_weights/THIRD_PARTY_NOTICES.md` at
   `apps/desktop/dist/aura-backend/_internal/demucs_weights/`. See
   dq3.md's own "Bundle verification" section for the exact sizes.
4. **Meter detection overhaul — INVESTIGATED, documented infeasible for
   now; defer.** Time-boxed investigation (2026-08-21) of three
   candidates, in order of promise, measured against the same fixture
   families the accent-comb adjudication used (6/8 sweep 40-140bpm step
   2, 2/4 sweep, legacy/generated 3/4 fixtures, the 14-clip melodic
   benchmark suite): **(1) madmom** (`RNNDownBeatProcessor`) — does NOT
   install cleanly on py3.11: PyPI ships no py3.11 wheel (last release
   0.16.1 predates 3.10), and even a from-source build (via
   `Cython`+`numpy` declared as `uv` extra-build-dependencies, the
   standard workaround) produces a package that fails on import with two
   separate, unpatched incompatibilities hit in its own eager import
   chain — `from collections import MutableSequence` (removed from the
   stdlib in Python 3.10+, used throughout `madmom.processors`, the base
   of every `Processor` class including the one needed here) and, after
   monkey-patching that, `np.float` (removed in numpy>=1.24, hit inside
   `madmom.io`). Fixing both would mean forking and patching the
   unmaintained upstream source, not a pinning change — genuinely
   infeasible on this stack. **(2) beat_this** (CPJKU's ISMIR'24 "Beat
   This!" tracker, pip name `beat-this`, pure-torch, MIT-licensed code
   *and* published weights) — installs cleanly (torch is already a
   dependency since DQ-2; note the plain PyPI resolution pulls a
   CUDA-enabled torch + ~4GB of `nvidia-*` packages unless both `torch`
   AND `torchaudio` are scoped to the `pytorch-cpu` index the way
   `workers/transcription/pyproject.toml` already scopes `torch` alone —
   scoping only `torch` was not sufficient in the scratch test), runs
   fast on CPU (~0.3-0.8s/clip, ~67s for 87 fixtures), but measured
   **9.2% (8/87) overall meter accuracy — worse than the existing 20%
   melodic-benchmark baseline.** Diagnosis: its downbeat head is
   unreliable on this project's fixture distribution (sparse click
   tracks and monophonic synthetic melodies are far out of its
   full-mix-real-recording training distribution) — predicted downbeats
   were frequently degenerate (median beats-per-bar collapsing to 1).
   Not a viable drop-in without fine-tuning on project-relevant audio,
   which was out of scope for this investigation. **(3) librosa-native,
   no new deps** — an onset-envelope autocorrelation margin (eighth-note
   periodicity minus beat periodicity, using the tracker's own measured
   beat period rather than a blind candidate sweep) targeted at the
   specific case the comb adjudication proved saturated (3/4 vs. 6/8).
   Using ground-truth tempo, this genuinely separates the two classes
   with non-overlapping margin ranges on the tested fixtures (3/4:
   [-1.01, -0.79], n=6; 6/8: [-0.29, 0.17], n=51) — including both
   tempi (100/110bpm) the comb adjudication documented as decisive
   *wrong-direction* failures. Using **detected** tempo
   (`librosa.beat.beat_track`, the realistic condition) against a wider
   3/4 sweep (n=106, both fixture generators, matching the 6/8 sweep's
   density) the ranges do overlap and the best single threshold reaches
   82.8% (130/157) overall — 3/4 recall is now near-ceiling (100%/98%
   across the two 3/4 fixture families) while 6/8 recall is 47.1%
   (24/51) at a fair (non-overfit) threshold — a real ~6x improvement
   over the comb's own 6/8 hit rate (4/51 decisive wins) at zero new
   dependency cost, but bottlenecked by `beat_track` itself locking onto
   the wrong periodicity on a meaningful fraction of these synthetic
   fixtures, which cascades directly into the margin. **None of the
   three clears this investigation's bar for a follow-up implementation
   proposal** (>80% on the cases the comb fails, decisively, not just
   in aggregate) — madmom is a hard install blocker, beat_this
   regresses accuracy, and the librosa-native lead is real but partial
   and gated on a beat-tracking-error problem this investigation did not
   solve. **Conclusion: defer the overhaul.** User correction via the
   inspector's `set_part_fact` meter picker remains the fallback, as it
   already was for 6/8/2/4. The librosa-native autocorrelation-margin
   lead (candidate 3) is worth a future scoped follow-up specifically
   aimed at the beat-tracking-error bottleneck (e.g. a more robust
   measure-level period estimate, or folding the margin into the
   existing Borda blend as an additional feature) rather than a
   from-scratch reattempt — full method, code, and per-fixture numbers
   for all three candidates are in the investigation's scratch
   prototypes and report (not committed to the repo — scratchpad-only
   investigation per this task's constraints).

## Release

**v1.2.0 IS LIVE** (published 2026-08-21T18:01Z, tag at main@d533831):
https://github.com/quyenanh198/AuraAudio/releases/tag/v1.2.0 —
`AuraAudio_1.2.0_amd64.deb` (1,123,919,168 B),
`AuraAudio_1.2.0_aarch64.dmg` (809,987,253 B),
`AuraAudio_1.2.0_x64_en-US.msi` (760,038,940 B). Installers now bundle
the piano CRNN checkpoint (172MB) and demucs htdemucs_6s weights (55MB)
— hence ~230-440MB heavier than v1.1.0. New since v1.1.0: the full
detection-quality program (benchmark harness; ghost-note filtering +
per-instrument thresholds, onset F1 0.540→0.971 on the original suite;
dedicated piano transcription model, real-piano F1 0.629→0.980; opt-in
demucs source separation, guitar-only, deterministic shifts=0), PDF
export, and the Windows PyInstaller path fix below. Attempt 1 (run
32503305604) failed ONLY on windows-msi: build-backend.sh under Git
Bash passed POSIX-style /d/a/... paths into PyInstaller --add-data,
which Windows read as drive-less paths; fixed in d533831 (cygpath -w +
';' separator on MINGW/MSYS, POSIX unchanged); attempt 2 (run
32509409553) green end-to-end. Demucs weights-license risk: accepted by
the product owner 2026-08-22 for personal-use context.

**v1.1.0 IS LIVE** (published 2026-08-20T01:21Z, run 32320010770 on
main@dfe5aa0, green on the first attempt):
https://github.com/quyenanh198/AuraAudio/releases/tag/v1.1.0 —
`AuraAudio_1.1.0_amd64.deb` (716,965,860 B),
`AuraAudio_1.1.0_aarch64.dmg` (482,564,744 B),
`AuraAudio_1.1.0_x64_en-US.msi` (439,654,237 B). New since v1.0.0:
dependency checking (deb Depends: ffmpeg; guided-install banner +
transcribe gating on macOS/Windows; GET /v1/system/deps) and YouTube
import via optional yt-dlp-on-PATH (the app's first network feature).
The Linux apt/ffmpeg mirror flake did NOT recur this time (it has hit 4
release runs to date; if it hits again, harden the ffmpeg install step —
e.g. a cached static binary — rather than re-dispatching a 5th time).

**v1.0.0 IS LIVE** (published 2026-08-19T22:35Z, run 32308624180 on
main@4ee7a25): https://github.com/quyenanh198/AuraAudio/releases/tag/v1.0.0
with three assets — `AuraAudio_1.0.0_amd64.deb` (716,942,612 B),
`AuraAudio_1.0.0_aarch64.dmg` (482,575,130 B),
`AuraAudio_1.0.0_x64_en-US.msi` (439,629,661 B). Attempt 1 (run
32303852041) lost only the Linux job to the recurring apt/ffmpeg mirror
flake; attempt 2 went green end-to-end. App version bumped 0.1.0→1.0.0
in `tauri.conf.json` + `src-tauri/Cargo.toml`/`Cargo.lock` (dc01a7e),
and scaffold Cargo metadata ("A Tauri App"/"you") replaced since it
lands in installer package metadata.

**Tag-push gotcha:** this session's git credentials can push branches
but NOT tag refs (stable HTTP 403 on `refs/tags/*`). The workflow
therefore accepts a `release_tag` input on `workflow_dispatch` (4ee7a25):
when set (e.g. `v1.0.0`), the tag-gated `release` job runs and
`softprops/action-gh-release` creates the tag at the run's commit if it
doesn't exist. Tag-push triggering still works for humans with normal
credentials.

Cutting a release: bump the version in
`apps/desktop/src-tauri/tauri.conf.json` (`productName`/`identifier`
already fixed as "AuraAudio" / `com.auraaudio.desktop` — only `version`
changes per release), commit it on `main`, then push a `vX.Y.Z` tag
matching that version. `.github/workflows/release.yml` builds the real
PyInstaller backend, `cargo tauri build`s the Linux `.deb`, macOS `.dmg`,
and Windows `.msi`, and attaches all three to a GitHub Release for that
tag. The same workflow also runs on `workflow_dispatch` (no tag) for
build-only testing — it uploads all three as workflow artifacts but does
not cut a Release in that mode. **The Windows `.msi` job is back** — see
item 2 below for the full two-part fix: a win32-scoped dependency-version
fix that unblocked `uv sync`, plus a second fix (found in code review,
after the first produced a green-but-broken build) that adds the real
`tensorflow-intel` backend package so the app's transcription feature
actually works on Windows, backed by a smoke-test CI step on all three
platforms so this class of gap can't ship silently again.

**Runtime note, all three platforms (RESOLVED for Linux, mitigated for
macOS/Windows):** none of the `.deb`, `.dmg`, or `.msi` bundles `ffmpeg`
itself — the backend (`aura_worker/stages/probe.py`/`normalize.py`/
`ffmpeg_utils.py`) still shells out to a system `ffmpeg`/`ffprobe` binary
at transcription-request time, and PyInstaller's `--onedir` bundle still
never collects it (only `--collect-data basic_pitch` is passed). What's
new: the Linux `.deb` now declares `Depends: ffmpeg` via
`bundle.linux.deb.depends: ["ffmpeg"]` in `tauri.conf.json` (verified
against the real Tauri v2 schema — `tauri-utils`'s `DebConfig.depends:
Option<Vec<String>>` nested under `LinuxConfig.deb` under
`BundleConfig.linux`, plus the bundled `@tauri-apps/cli/config.schema.json`
— not assumed from memory), so `apt`/`dpkg` installs ffmpeg automatically
alongside the app on Linux; no build was run to confirm the resulting
`.deb`'s control file (JSON validity + schema-path verification only), so
the next CI `.deb` build is this change's real verification. macOS and
Windows still can't auto-install a system package this way, so instead the
app now detects the gap at runtime: a new `GET /v1/system/deps` backend
endpoint (`apps/api/src/aura_api/routers/system.py`) reports
`shutil.which`/`<bin> -version` results for both binaries, and the Home
screen checks it on mount (`apps/desktop/web/src/lib/deps.ts`), showing a
dismissal-free warning banner when either is missing — which binary is
missing, a one-line per-OS install command with a Copy button (Windows:
`winget install Gyan.FFmpeg`; macOS: `brew install ffmpeg`; Linux: `sudo
apt install ffmpeg`, covering the pre-.deb-fix case and any other Linux
packaging), and a "Check again" button — and disables the Guitar/Piano
transcription-start buttons (with an explanatory tooltip) while deps are
missing. Users on macOS/Windows still need to run the suggested command
themselves and click "Check again"; true bundling (vendoring a static
ffmpeg binary into the PyInstaller bundle) remains future work, not done
here.

**Current ship-readiness roadmap** (user-approved order, in progress):
1. **Branding + release workflow — DONE.** Real app icon (amber
   waveform-into-eighth-note mark on #1e1d21, generated via SVG→Chromium
   raster + `cargo tauri icon`); productName/version 0.1.0; favicon +
   README de-scaffolded. The release workflow's first green
   `workflow_dispatch` run is **32236862998**: build job succeeded in
   15m53s producing `AuraAudio_0.1.0_amd64.deb` (684M) as artifact;
   the tag-gated `release` job correctly skipped on a non-tag ref.
   Getting there took 6 runs against a flaky runner day — real fixes:
   apt per-connection timeout (`Acquire::http::Timeout=15`) for silent
   mirror stalls, and `cargo tauri build --bundles deb` (the default
   bundler finished all real work in ~4 min then hung ~57 min fetching
   AppImage tooling). NOTE: `tauri.conf.json` `bundle.targets` is still
   "all" — a LOCAL `cargo tauri build` without `--bundles deb` can hit
   the same AppImage hang; scope it or pass the flag.
2. **Windows `.msi` + macOS `.dmg` jobs — DONE, all three platforms
   genuinely green (including a real ML inference backend on Windows).**
   Windows was previously reported CI-infeasible (see below for the
   original blocker). Getting it working took **two** separate fixes, not
   one — the first produced a run that was green in CI but shipped a
   `.msi` with a dead transcription feature, caught in code review before
   merge, not after. Recorded honestly below because the first fix's own
   write-up in this doc was wrong and needs correcting, not just
   superseding.

   **Fix round 1 (produced a false green):** `uv sync` was failing to
   resolve a Windows wheel for `tensorflow-io-gcs-filesystem` (full
   original-blocker evidence below). Fixed by adding
   `tool.uv.constraint-dependencies = ["tensorflow-io-gcs-filesystem<=0.31.0 ;
   sys_platform == 'win32'"]` to the workspace root `pyproject.toml`,
   scoped via a PEP 508 marker so only the win32 resolution changed
   (`git diff uv.lock` confirmed Linux/darwin byte-identical). This alone
   made `windows-msi` pass — run **32273747500** produced a `.msi`, all
   three jobs green, `release` correctly skipped on the non-tag ref. It
   was reported here as fully DONE, with the resulting `.msi`'s
   suspiciously small size (160M vs the `.deb`'s 684M and `.dmg`'s 464M)
   explained as "smaller Windows tensorflow wheel + LZMA compression."
   **That explanation was wrong**, and the investigation behind it was not
   thorough enough to have caught it: it looked at whether `tensorflow`
   was "installed" and whether PyInstaller was "bundling" it, but never
   actually ran the resulting code path.

   **What was actually wrong, found in code review:** on Windows, PyPI's
   `tensorflow==2.14.0` wheel is a **metadata-only stub with zero Python
   code**. Its real implementation is a separately-named package,
   `tensorflow-intel`, pulled in only via a marker inside the STUB's own
   METADATA (`Requires-Dist: tensorflow-intel (==2.14.0) ;
   platform_system == "Windows"`) — a marker uv's resolver does not expand
   transitively through basic-pitch's `tf` extra, since that extra only
   names bare `tensorflow`/`tensorflow-macos`. Result: `tensorflow`
   "installed" successfully, `uv sync` and PyInstaller both completed
   without error, and the only symptom anywhere in the CI logs was one
   easy-to-miss line in the PyInstaller analysis output:
   `WARNING:root:Tensorflow is not installed. If you plan to use a TF
   Saved Model, reinstall basic-pitch with 'basic-pitch[tf]'` — present in
   run 32273747500's `windows-msi` log the whole time, never checked for.
   basic-pitch only warns and continues when no inference backend is
   importable, so this shipped a fully green `.msi` whose transcription
   feature would fail on every real request.

   **Fix round 2 (the real fix):** added
   `tensorflow-intel==2.14.0 ; sys_platform == 'win32'` as an explicit
   dependency in `workers/transcription/pyproject.toml` — a package uv's
   resolver cannot add on its own from a constraint, only a real
   dependency entry does that. `uv lock` pulled in the genuine ~271MB
   win_amd64 wheel; a package-identity diff of old vs new `uv.lock` (name,
   version, wheel hashes, ignoring purely-structural marker-string
   rewrites) confirmed `tensorflow-intel==2.14.0` is the ONLY added entry
   — every other package's version and wheel hashes stayed byte-identical.
   Also added a **"Smoke-test ML inference backend" step to all three**
   build jobs (`build`, `macos-dmg`, `windows-msi`), right after "Install
   python dependencies": `uv run --package aura-worker python -c "import
   tensorflow as tf; import basic_pitch.inference; print('tf',
   tf.__version__)"`. This actively fails the job (rather than just
   warning) if any platform ever again ends up with a stub/non-functional
   backend — the class of bug that let the first false green through.

   **Confirming, now-genuinely-green run: 32276566356** (head `609ab41`).
   All three jobs green including the new smoke-test steps; `release`
   correctly skipped on the non-tag ref:
   - `build` (Linux): `.deb` 684M (716,967,690 bytes); smoke test printed
     `tf 2.14.0`.
   - `macos-dmg`: `.dmg` 464M (482,580,836 bytes); smoke test printed
     `tf 2.14.0`.
   - `windows-msi`: **`.msi` 420M (439,625,565 bytes)** — up from the
     false green's 160M, now the expected order of magnitude once the
     real ~271MB `tensorflow-intel` wheel is actually bundled. Smoke test
     printed `tf 2.14.0`. The full PyInstaller analysis log for this run
     was checked line by line for backend warnings: **zero**
     `Tensorflow is not installed` lines anywhere (the only remaining
     warnings are for `coremltools`/`tflite-runtime`/`onnxruntime`, which
     are genuinely not installed and not needed since the real TF backend
     is present).

   Verified locally before pushing fix round 2: `uv sync --all-packages
   --all-extras` and the full `apps/desktop` (10/10) and
   `workers/transcription` (69/69) test suites pass unchanged on Linux.

   **Lesson for future work in this doc:** "the build is green" and "the
   feature works" are different claims, and the gap between them can hide
   in a warning line in a multi-thousand-line log rather than an error.
   The smoke-test steps now make this class of gap fail loudly instead of
   silently; don't remove them as a build-time optimization without
   replacing what they check for.

   `http://tauri.localhost` (the Windows/Android WebView2 production
   origin, per `tauri-2.11.5/src/manager/mod.rs`'s `cfg!(windows)`
   branch) is now in `WEBVIEW_ORIGINS` in `apps/desktop/run_backend.py`,
   with a matching `test_v1_allows_windows_webview_origin` test in
   `apps/desktop/tests/test_cors_scope.py` — this ruling now matters for
   real: the Windows CI build below is green and produces an installable
   `.msi`. `apps/desktop/src-tauri/src/backend.rs`'s
   `resolve_backend_executable` also now resolves `aura-backend.exe` on
   Windows vs `aura-backend` elsewhere (`BACKEND_EXE_NAME`, `cfg(windows)`)
   — a real correctness bug (PyInstaller names the Windows executable
   with `.exe`; the old code hardcoded the extensionless name) fixed
   ahead of when it'll actually matter. Note: this session still had no
   Windows machine to install the `.msi` and smoke-test the running app
   end-to-end — only to confirm the CI build succeeds and the bundled
   backend genuinely imports `tensorflow`/`basic_pitch.inference`. A
   future reviewer with Windows access installing the `.msi` and running a
   real transcription is the remaining gap between "CI proves the backend
   imports" and "a user can transcribe a file."

   **macOS (`macos-dmg`, macos-latest/arm64): DONE, real green run.**
   Mirrors the Linux job (uv/Python 3.11, npm build, `bash
   build-backend.sh`, `cargo tauri build --bundles dmg`). Required one
   real fix first: basic-pitch 0.4.0's PyPI metadata has a marker bug —
   its bare `tensorflow-macos` dependency only activates for
   `python_version > "3.11"`, so macOS + this project's Python 3.11 pin
   (forced by tensorflow's own cp311-only wheels) resolved NEITHER tensorflow
   package at all, even though a matching
   `tensorflow_macos-2.14.0-cp311-cp311-macosx_12_0_arm64.whl` exists on
   PyPI. Fixed by opting `workers/transcription/pyproject.toml`'s
   `basic-pitch` dependency into basic-pitch's own `tf` extra
   (`basic-pitch[tf]`), whose `tensorflow-macos` marker
   (`python_version > "3.7"`) doesn't have the bug — confirmed directly
   against PyPI's `requires_dist` and against `uv.lock`'s resolution
   before/after. No tensorflow version was changed. First real green run:
   **32240106566** (dispatched to `claude/multi-ai-skills-caveman-7tx5l0`
   at commit `c9dd524`) — `macos-dmg` produced a `.dmg` artifact
   (`auraaudio-macos-dmg`, ~456M) alongside Linux's `.deb` staying green
   in the same run; the tag-gated `release` job correctly skipped on the
   non-tag ref. (This platform did NOT have the stub-wheel problem:
   `tensorflow-macos`'s PyPI wheel is a real implementation, not a
   metadata stub — confirmed by the smoke-test step passing here on the
   very first fix round, with no second round needed.)

   **Windows original blocker, and why it's now fixed:** run
   **32239242311**'s `windows-msi` job failed `uv sync --all-packages
   --all-extras` in 2 seconds, before touching Rust/Tauri/PyInstaller at
   all:
   ```
   error: Distribution `tensorflow-io-gcs-filesystem==0.37.1` can't be
   installed because it doesn't have a source distribution or wheel for
   the current platform
   hint: You're on Windows (`win_amd64`), but `tensorflow-io-gcs-
   filesystem` (v0.37.1) only has wheels for: manylinux_2_17_aarch64,
   manylinux2014_aarch64, manylinux_2_17_x86_64, manylinux2014_x86_64,
   macosx_10_14_x86_64, macosx_12_0_arm64
   ```
   This is a DIFFERENT shape of problem than the macOS gap above (which
   was a marker bug excluding a wheel that genuinely exists). Here,
   verified directly against PyPI's file listing across
   `tensorflow-io-gcs-filesystem` 0.34.0/0.35.0/0.36.0/0.37.0/0.37.1: the
   package shipped its LAST `win_amd64` wheel at **0.31.0** (April 2023)
   and has shipped **zero** Windows wheels of any kind at every version
   since. `tensorflow==2.14.0` itself has a real `win_amd64` cp311 wheel
   (confirmed in `uv.lock`) and only requires
   `tensorflow-io-gcs-filesystem>=0.23.1` (no upper bound), so uv
   correctly resolved the latest release — which has no Windows wheel.

   At the time (this same session, earlier), forcing
   `tensorflow-io-gcs-filesystem` back to `<=0.31.0` globally was correctly
   identified as a real downgrade of an ML-adjacent transitive dependency
   by over a year, and NOT attempted — global was the wrong scope, since
   0.31.0 lacks a macosx arm64 wheel and would have broken the (then newly
   green) `macos-dmg` job. The `windows-msi` job was removed from
   `release.yml` rather than left permanently red (a failing `needs:`
   dependency would have permanently blocked the tag-gated `release` job),
   with the blocker preserved inline as a comment where the job used to be
   and full step-by-step definition kept in git history at commit
   `1c8c073`.

   **This is now resolved** — see "Fix round 1" and "Fix round 2" above
   for the full two-part story (the `tensorflow-io-gcs-filesystem` wheel
   gap AND the separate `tensorflow-intel` stub gap it uncovered) and run
   **32276566356** for the genuinely-green confirming result.
3. **Editing v2 / meter expansion — DONE (meter expansion half; the rest of
   "editing v2" — structure ops, multi-select, drag editing, MIDI-in —
   remains unchosen/future work).** 10 manually-settable meters, 4
   auto-detected, one source of truth. Full story below.

### Meter expansion (roadmap item 3 — DONE)

Spec: `docs/superpowers/specs/2026-08-19-meter-expansion-design.md`. Plan:
`docs/superpowers/plans/2026-08-19-meter-expansion.md` (7 tasks, all
committed and reviewed on `claude/multi-ai-skills-caveman-7tx5l0`).

**What shipped.** The pipeline went from two hardcoded meters (4/4, 3/4,
duplicated across four files) to a single source of truth:
`score_schema.meters` (new module) owns `SUPPORTED_METERS` — the 10
meters a user can set manually via edit/validate/export: `2/4, 3/4, 4/4,
5/4, 2/2, 3/8, 6/8, 7/8, 9/8, 12/8` — and `DETECTABLE_METERS` — the 4 the
structure stage auto-detects: `4/4, 3/4, 6/8, 2/4` (a subset, asserted by
a test). It also owns the meter-math helpers (`beats_per_measure`,
`is_compound`, `notated_beats`) that used to be duplicated or hardcoded
inline. `validate.py`'s part `meter` enum, `edits.py`'s `set_part_fact`
guard, and the worker's `structure.py`/`quantize.py` all import from this
one module now; nothing else defines a meter list. `STAGE_VERSION` bumped
in both worker stages whose output semantics changed: `structure.py`
1→2 (new detection candidates), `quantize.py` 3→4 (meter-generic measure
bucketing replaces a `METER_CANDIDATES`-keyed lookup, which is deleted
entirely — Fraction-based `beats_per_measure` handles all 10 meters,
including the non-integral ones like 7/8). MusicXML export needed no code
change — `_measure_length_ql` was already generic — just round-trip test
coverage for all 10 meters × guitar/piano. The frontend
(`apps/desktop/web/src/lib/noteEdit.ts`) mirrors `SUPPORTED_METERS` as
`METER_OPTIONS`, pinned by a Vitest test on both sides so a drift on
either side fails that side's suite — the repo's established
mirror-constant pattern (same as the existing noteEdit ↔ edits.py
mirror). `Sidebar.svelte`'s meter `<select>` now offers all 10; no new
UI, no API shape change, no schema version bump (the widened enum still
accepts old 4/4-or-3/4 scores unchanged).

**6/8 detection: honest and narrow, not general.** Distinguishing 6/8
from 3/4 by beat-accent scoring is a subharmonic-alias problem — a
genuine 3/4 clip's period-3 accent pattern aliases into the period-6 comb
6/8's detector needs, so the two scoring signals `_detect_meter` blends
(peak margin, mean margin) can genuinely disagree with each other on most
tempi, not just with the ground truth. After a fix-round adjudication
that tried several alternative discriminators (tie-break by mean_margin
magnitude, a modified peak_margin, ratio thresholds, scaled compound
margins) and found none of them separate "real 6/8" from "3/4 aliased
into 6/8" without breaking the other case, the shipped detector keeps the
simple `DETECTABLE_METERS`-declared-order tie-break (4/4 first) rather
than pretending a cleverer rule generalizes, and the test suite asserts
only what was actually measured: a tempo sweep over `range(40, 141, 2)`
bpm found just **{50, 62, 100, 124} bpm** win 6/8 decisively (not by
tie-break) — a narrow, non-contiguous, but real and reproducible set
(`test_detects_6_8_across_validated_tempos`). 4/4 detects reliably across
a wide tempo range — a throwaway sweep of the shipped 2/4 fixture over
70-140 bpm in 10 bpm steps confirmed 4/4 correct at all 8 points. **2/4
is NOT reliable**, the same throwaway sweep found: correct at 70/110/
120/130 bpm, but wrong at 80 bpm (→ 3/4), 90 bpm (→ 4/4), 100 bpm (→
6/8), and 140 bpm (→ 6/8) — 4 wrong out of 8. Like 6/8, treat a detected
2/4 as a hint, not a fact: correct it via the inspector's `set_part_fact`
meter picker when wrong, the same path already used for any other
detected meter.

**The bidirectional-risk caveat (unchanged from the fix-round that wrote
it — preserved here verbatim):** 6/8 vs. 3/4 miscategorization is
bidirectional, not one-way: it's a subharmonic-alias case (a 3/4 clip's
period-3 accent pattern aliases into the period-6 comb 6/8 needs) that
the two scoring signals `_detect_meter` blends genuinely disagree on at
most tempi. A genuine 6/8 clip may come back tagged 3/4 outside the
narrow validated tempo set (see `test_detects_6_8_across_validated_tempos`);
separately, a genuine 3/4 clip at certain tempos (measured: bpm 100/110
on the legacy `write_metronome_pulse_wav` fixture) can come back tagged
6/8 *decisively*, not just via the conservative tie-break — the alias
margins outright win both scoring signals there. Either direction
silently misdirects downstream score structure (measure grouping,
beaming). Until this is hardened further, treat detected 6/8 or 3/4 as a
hint and correct via the inspector's `set_part_fact` meter picker when
wrong — the same path already used for any other detected meter.

**Verification (Task 7, full workspace sweep).** All five Python suites
green: `score_schema` 173, `test_fixtures` 16, `musicxml` 47, `aura-worker`
91, `aura-api` (`apps/api/tests` + `apps/desktop/tests`, run as two
separate invocations per this repo's own `Makefile` — see gotcha below)
48 + 10; frontend Vitest 141. The repo's `test_e2e_pipeline.py` (full
upload→transcribe→structure→quantize→assign→export pipeline, which calls
`assign.py`'s real `validate_score()` — the same widened
`SUPPORTED_METERS` enum) passed both its idempotency and piano-grand-staff
cases, confirming a full pipeline run produces a valid score under the
widened enum. Two deferred minors from earlier task reviews were cleaned
up as sanctioned small fixes: the musicxml guitar export test now asserts
the full `TimeSignature` set (mirroring the piano test) instead of only
`ts[0]`; and `test_fixtures.generate.generate_metered_clicks`'s compound
secondary-accent index generalized from `grid_size // 2` (which landed
mid-group for 9/8, index 4) to every dotted-quarter group start
(`range(3, grid_size, 3)` — 3 for 6/8, {3, 6} for 9/8, {3, 6, 9} for
12/8), with 6/8's behavior kept byte-identical.

**Gotcha found during verification, NOT meter-expansion fallout:**
running `apps/api/tests` and `apps/desktop/tests` in a single combined
`pytest` invocation (as the plan's Task 7 Step 1 literally suggests)
produces ~20-29 spurious failures/errors from cross-file test pollution —
`apps/desktop/tests/test_cors_scope.py` sets `AURA_DATA_DIR` /
`DATABASE_URL` unconditionally at *module import time* (by design, see
its own docstring — a deliberate anti-`setdefault` choice, not a bug in
isolation), and when both test directories are collected into the same
pytest session this clobbers `apps/api/tests/conftest.py`'s own env
setup for tests collected afterward. Reproduced on a clean pre-meter-
expansion checkout (commit `c88f193`, before any of this sub-project's
work) via a throwaway `git worktree` — confirms this is pre-existing and
unrelated to the meter work. This repo's own `Makefile` already runs
these two directories as two separate `pytest` invocations (never
combined), which is what this verification pass used, and both pass
cleanly that way (48 and 10 respectively). Left unfixed as out of scope
for this sub-project; worth a small follow-up (e.g. an `autouse` fixture
resetting the env vars, or simply keeping the two suites permanently
separate in any future combined-sweep tooling).

## Quick start for a fresh session

```bash
cd /home/user/AuraAudio
source .envrc && make test   # expect all six packages green (386/386 as of this fix-round's
                              # verification: score_schema 173, test_fixtures 16, musicxml 47,
                              # aura-worker 92, aura-api apps/api 48 + apps/desktop 10 — run as
                              # two separate pytest invocations per this repo's own Makefile, see
                              # the cross-file test-pollution gotcha above. The frontend Vitest
                              # suite (141/141) is not part of `make test` — run it separately via
                              # `npm run test` in apps/desktop/web. These are the counts as of this
                              # fix-round, not the 151/151 recorded for sub-project 3's task 9
                              # above — they will drift again as the codebase grows, so re-run and
                              # recount rather than trusting either number blindly.)
```

No external services to start first — see "Environment gotchas" above.
