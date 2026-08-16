# Session Handoff

Read this first in any new session picking up AuraAudio work. It exists so a
fresh session (with no memory of prior conversations) can orient in one read
instead of re-deriving decisions from `git log`.

## What AuraAudio is

Converts an uploaded guitar/piano audio clip into an editable score
(MusicXML + MIDI). Full product design lives in `ARCHITECTURE.md` (repo
root) — a 4-phase plan. **Phase 1 (vertical slice) is done and merged.**
Phase 2 is in progress, built as independent sub-projects, each with its own
spec → plan → implementation cycle (see "Working process" below).

## Repo layout

```text
apps/api/                      FastAPI service: projects, jobs, exports
workers/transcription/          Worker: probe -> normalize -> inference ->
                                structure -> quantize -> export
packages/score_schema/          Canonical score JSON contract (schemaVersion 2)
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

Full workspace test suite: **92/92 passing.** Working tree clean, `main`
in sync with `origin/main` (last pushed commit: `15305e4`).

## Phase 2 sub-projects remaining (in the order originally proposed)

1. ~~Beat/meter/key intelligence~~ — **done** (above).
2. ~~Guitar string/fret assignment~~ — **fully done**, including the final
   whole-branch review and its fix wave. See the "Status: what's done"
   section above for full detail.
3. **Piano hand/staff assignment** — split out from item 2 above. Own spec
   needed (hand/staff split-point optimizer, different algorithm than
   guitar's string/fret DP — see `ARCHITECTURE.md` §4.3). Not started.
4. **Web client + SVG score preview** — doesn't exist at all yet. Needs its
   own product/UI brainstorm from scratch, not just a plan.
5. **PDF rendering** — new export format, isolated renderer process.
6. **Offline benchmark pipeline** — CI/scheduled eval harness.

Recommendation if picking up cold: start #3 (piano hand/staff assignment)
to close out the assignment pair before moving to the web client, which is
a much bigger scope jump (needs its own brainstorm, not just a plan). Use
the same process as sub-projects 1 and 2: brainstorming → spec → plan →
subagent-driven-development → final whole-branch review.

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
source .envrc && make test   # expect 66/66 passing
```
