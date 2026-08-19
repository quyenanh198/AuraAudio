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

## Release

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
item 2 below for the win32-scoped dependency fix that unblocked it.

**Runtime note, all three platforms (not a regression from this work,
pre-existing on Linux too):** none of the `.deb`, `.dmg`, or `.msi`
bundles `ffmpeg` — the backend
(`aura_worker/stages/probe.py`/`normalize.py`/`ffmpeg_utils.py`) shells
out to a system `ffmpeg`/`ffprobe` binary at transcription-request time,
and PyInstaller's `--onedir` bundle never collects it (only
`--collect-data basic_pitch` is passed). The Linux `.deb`'s own metadata
declares no dependency on ffmpeg either (`tauri.conf.json` sets no
`bundle.linux.deb.depends`), so this isn't a new gap introduced here —
just now also true for macOS and Windows. Users on any platform need
ffmpeg on PATH themselves (on Windows, that means a real ffmpeg.exe on
`PATH`, e.g. via winget/choco or a manual download); documenting/bundling
it is future work, not done here.

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
   green.** Windows was previously reported CI-infeasible (see below for
   the original blocker); it's now fixed via a deliberate, user-approved
   win32-scoped dependency pin. Final confirming run **32273747500** (head
   `c6bb60a`): Linux `.deb` 684M (716,960,744 bytes) + macOS `.dmg` 464M
   (482,581,698 bytes) + Windows `.msi` 160M (167,308,116 bytes) all
   green, tag-gated `release` correctly skipped on the non-tag ref. A
   prior dispatch on the same commit, run **32269927423**, had already
   proven `windows-msi` green on its first attempt (identical 167,308,116
   byte `.msi`, byte-for-byte reproducible across both runs) while `build`
   (Linux) hit a transient `apt-get install ffmpeg` mirror stall unrelated
   to this change (the same known flaky-mirror class already handled by
   that step's own retry/timeout logic elsewhere in this doc) — re-dispatching
   produced the fully green confirming run above without further code
   changes.

   **Windows (`windows-msi`, windows-latest): DONE, real green run, fixed
   via a user-approved win32-scoped constraint.** The original blocker
   (below) — `uv sync` failing to resolve a Windows wheel for
   `tensorflow-io-gcs-filesystem` — is fixed by adding
   `tool.uv.constraint-dependencies = ["tensorflow-io-gcs-filesystem<=0.31.0 ;
   sys_platform == 'win32'"]` to the workspace root `pyproject.toml`. This
   pins the resolution back to the last version that shipped a `win_amd64`
   wheel (0.31.0, Apr 2023), scoped ONLY to `sys_platform == 'win32'` via a
   PEP 508 marker — `git diff uv.lock` confirms the win32 resolution forks
   to 0.31.0 while every other platform (Linux, macOS/darwin) stays on
   0.37.1, byte-identical to before. This is a real, deliberate, ~1.5-year
   downgrade of an ML-adjacent transitive dependency — accepted because
   AuraAudio is 100% offline and never performs the Google Cloud Storage
   I/O this package exists to provide; there is no runtime behavior
   difference for this app between 0.31.0 and 0.37.1. Verified before
   pushing: `uv sync --all-packages --all-extras` and the full
   `apps/desktop` (10/10) and `workers/transcription` (69/69) test suites
   all pass unchanged on Linux.

   The `.msi`'s size (160M) is notably smaller than the `.deb` (684M) and
   `.dmg` (464M) built from the same commit. Investigated, not just
   accepted: the job's "Install python dependencies" step logs show
   `tensorflow==2.14.0` and `tensorflow-io-gcs-filesystem==0.31.0` were
   genuinely installed (not skipped), and PyInstaller's analysis log shows
   it actively processing `hook-tensorflow.py` and bundling the real
   package. WiX's `light.exe` linker ran for ~71s compressing the staged
   backend into the final `.msi`, consistent with real compression work
   over a multi-hundred-MB payload, not a trivial/empty bundle. The
   leading explanation is that PyPI's `win_amd64` wheel for `tensorflow`
   2.14.0 is itself substantially smaller than its Linux `manylinux`
   counterpart (Windows builds exclude some of the larger XLA/TensorRT-
   adjacent binary content Linux wheels ship), compounded by WiX's LZMA
   cabinet compression. The `.msi` size was also byte-identical
   (167,308,116 bytes) across two independent runs, which argues against a
   flaky/partial build. **Flagged here for a future reviewer to double-check
   by actually installing the `.msi` and running the app** — this session
   did not have a Windows machine available to smoke-test the installed
   result end-to-end, only to confirm the CI build succeeds and produces a
   plausible, reproducible artifact.

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
   ahead of when it'll actually matter.

   **macOS (`macos-dmg`, macos-latest/arm64): DONE, real green run.**
   Mirrors the Linux job (uv/Python 3.11, npm build, `bash
   build-backend.sh`, `cargo tauri build --bundles dmg`). Required one
   real fix first: basic-pitch 0.4.0's PyPI metadata has a marker bug —
   its bare `tensorflow-macos` dependency only activates for
   `python_version > "3.11"`, so macOS + this project's Python 3.11 pin
   (forced by tensorflow's cp311-only wheels) resolved NEITHER tensorflow
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
   non-tag ref.

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

   **This is now resolved**, per the explicit user decision recorded at the
   top of this roadmap item: scope the same downgrade to
   `sys_platform == 'win32'` only, via a `tool.uv.constraint-dependencies`
   marker in the workspace root `pyproject.toml`, verified via `git diff
   uv.lock` to leave every other platform's resolution untouched. The
   `windows-msi` job was restored from its `1c8c073` definition (comment
   rewritten to match) and re-added to the tag-gated `release` job's
   `needs:` list and artifact glob. See the confirming run above
   (**32273747500**) for the green result.
3. **Editing v2 / meter expansion** — direction to be chosen by the user
   (structure ops, multi-select, drag editing, MIDI-in / more meters).

## Quick start for a fresh session

```bash
cd /home/user/AuraAudio
source .envrc && make test   # expect all six packages green (151/151 as of sub-project 3's task 9;
                              # apps/desktop's suite joined the other five during sub-project 2)
```

No external services to start first — see "Environment gotchas" above.
