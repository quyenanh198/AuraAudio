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
   process. Backend-only, no UI. **This is the sub-project currently being
   brainstormed** — check for a spec at
   `docs/superpowers/specs/*offline-backend*` or a plan at
   `docs/superpowers/plans/*offline-backend*`; if neither exists yet, the
   brainstorming conversation itself is the only record of progress so far
   (see the bullet list above) and needs to be resumed/redone.
2. **Desktop shell + packaging** — Tauri wrapper spawning the Python
   backend as a managed sidecar, native window at localhost. No real UI.
3. **Score preview + playback UI** — upload flow, SVG notation rendering
   (likely via an existing MusicXML-to-SVG renderer rather than building
   one from scratch — not yet decided) synced to audio playback, export.
   Real product/UI design work; plan to use the brainstorming skill's
   visual companion tool for this one.
4. **Semantic editing** — edit-operation model (add/delete/move note,
   undo/redo, optimistic locking, locks) plus the UI to drive it. Biggest
   single piece, builds on 1-3.

None of the four have a written spec yet as of this update — only the
scoping/technology decisions above have been made in conversation.

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
direction — see "Direction change" near the top of this document for the
current plan (4 sequential sub-projects, first one — offline backend
adaptation — is being brainstormed now). PDF rendering and an offline
benchmark CI harness are still real future work, just not sequenced yet;
revisit once the desktop app's 4 sub-projects are further along.

**Known follow-up, not yet its own sub-project:** `musicxml/export.py`
appends notes to each measure/staff in list order rather than sorting by
`notatedOnset` first — real transcribed audio's event list isn't
guaranteed to already be onset-sorted (confirmed via a real e2e run), so
exported rhythm can come out scrambled for both guitar and piano today.
Predates sub-projects 2 and 3; caught (but correctly ruled out of scope)
by sub-project 3's final review. Worth a small bounded fix on its own —
sort each measure's events by `notatedOnset` before building notes — before
it's forgotten as "always been like that." Good candidate to fold into
sub-project 1 (offline backend adaptation) or knock out separately first,
since it's small and bounded either way.

Recommendation if picking up cold: read "Direction change" at the top of
this document first — it supersedes the framing below. Resume brainstorming
sub-project 1 (offline backend adaptation) if no spec exists yet for it.

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

- **Docker Hub image pulls are blocked** by this sandbox's egress policy
  (`production.cloudfront.docker.com` denied). `docker-compose.yml` and
  both Dockerfiles are correct for real deployment but were never build-
  verified live here. Local dev/test substitutes: native `postgres`
  (`service postgresql start`), native `redis-server`
  (`service redis-server start`), and `moto[server]` as an S3-compatible
  MinIO stand-in (`/opt/moto-venv/bin/moto_server -p 9000` — needs its own
  venv since it conflicts with system Python packages; bucket `aura-media`
  must be created once per restart via boto3).
- **These services do not persist across session/container restarts** —
  if `redis-cli ping`, `pg_isready`, or `curl 127.0.0.1:9000` fail at the
  start of a new session, restart them with the commands above before
  running tests.
- **`.envrc`** at repo root holds `DATABASE_URL`/`REDIS_URL`/`S3_*` env
  vars. Bash tool shell state does not persist between tool calls in this
  harness — every test-running command needs `source .envrc &&` prefixed,
  and use absolute paths for pytest file arguments (`uv run --package X`
  resolves relative paths against the invoked package's own directory in
  a way that's easy to get wrong).
- `setuptools<81` is pinned in `workers/transcription/pyproject.toml` —
  newer setuptools dropped `pkg_resources`, which `basic-pitch`'s
  `librosa`/`numba`/`resampy` chain still imports at runtime.

## Quick start for a fresh session

```bash
cd /home/user/AuraAudio
service postgresql start && service redis-server start
(/opt/moto-venv/bin/moto_server -p 9000 &) ; sleep 2
/opt/moto-venv/bin/python -c "
import boto3
c = boto3.client('s3', endpoint_url='http://127.0.0.1:9000', aws_access_key_id='aura', aws_secret_access_key='aurasecret', region_name='us-east-1')
c.create_bucket(Bucket='aura-media')
"
source .envrc && make test   # expect 112/112 passing
```
