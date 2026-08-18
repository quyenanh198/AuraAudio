# Semantic Editing — Design Spec

Date: 2026-08-18
Status: Approved (sub-project 4 of the desktop pivot)
Predecessors: offline backend adaptation (sub-project 1), desktop shell + packaging (sub-project 2), score preview + playback UI (sub-project 3 — all merged to main).

## 1. Context and Goal

AuraAudio's desktop app now transcribes a recording and shows it as an
OSMD-rendered score with synced playback. Transcription is imperfect;
the user needs to fix it in place. This sub-project adds semantic
editing: select a note on the score and change what it *means* (pitch,
timing, fingering, hand), delete or add notes, correct the detected
key/tempo/meter, and undo/redo any of it — with the notation, exports,
and both playback sources always reflecting the edited state.

"Semantic" means operations on the score JSON's musical model, never on
raw MusicXML. The backend remains the single source of truth: every
edit round-trips through it (approach A, chosen over frontend-local
editing to avoid porting the exporter/fingering pipeline to TypeScript,
and over a hybrid until latency is measured).

## 2. Non-Goals

- Measure split/merge, voice reassignment (structure ops — deferred).
- Multi-select, copy/paste, drag-on-canvas manipulation.
- Re-running transcription/inference on edited regions.
- MIDI keyboard input.
- Collaborative/concurrent editing (single local user).

## 3. Scope of Edit Operations (v1)

| Op | Payload | Effect |
|---|---|---|
| `set_pitch` | `{eventId, pitch}` | MIDI pitch change |
| `move_note` | `{eventId, notatedOnset}` | Move onset on the grid (within its measure) |
| `set_duration` | `{eventId, notatedDuration}` | Duration on the grid |
| `delete_note` | `{eventId}` | Remove event |
| `add_note` | `{measureNumber, notatedOnset, notatedDuration, pitch, voice}` | New event; id server-generated. v1 only into measures that already exist in the score JSON (the pipeline emits no fully-silent measures); otherwise 422 |
| `set_fingering` | `{eventId, string, fret}` | Guitar override |
| `set_hand` | `{eventId, hand}` | Piano override |
| `set_locked` | `{eventId, locked}` | Pin/unpin against DP re-assignment |
| `set_part_fact` | `{field: "key"\|"tempoBpm"\|"meter", value}` | Corrects a detection fact |

Every op that touches a note sets that note's `locked: true` (the
schema-v4 flag reserved for exactly this) unless the op is
`set_locked: false`. `onsetSeconds`/`offsetSeconds` for
touched/added notes are recomputed from the part's `timeMap` so
playback sync and synth scheduling stay correct.

## 4. Backend

### 4.1 Edit application

- New pure-mutation module (in `packages/score_schema`, e.g.
  `score_schema.edits`): `apply_edit(score: dict, op: EditOp) -> dict`
  returns a NEW score dict (immutability rule), validated with the
  existing `score_schema.validate` before persisting. Invalid ops raise
  a typed error carrying a human-readable reason.
- New router `apps/api/src/aura_api/routers/edits.py`:
  - `POST /v1/projects/{id}/edits` — body is one typed op (shape above).
    Applies to the HEAD score revision (bootstrapping the baseline from
    the `assign` artifact on first edit), inserts the next
    `ScoreRevision`, updates the head pointer, enqueues a
    re-derive job (4.3), returns `{version, score, rederiveJobId}`
    (the edited JSON so the inspector updates instantly; the job id is
    observable via the existing `GET /v1/jobs/{id}` for the
    "updating…" state).
  - `POST /v1/projects/{id}/edits/undo` and `/redo` — move the head
    pointer down/up the version chain (no new version written),
    enqueue re-derive, return `{version, score, rederiveJobId}`.
  - `POST /v1/projects/{id}/edits/revert` — head pointer to the
    original transcription's version.
  - 404 for missing project/score; 422 with `{detail}` for invalid ops
    (out-of-range pitch, onset outside measure, unknown eventId);
    409 for undo at the oldest version / redo at the newest.

### 4.2 Versioning and the head pointer

- Edit history reuses the EXISTING `ScoreRevision` table (project-scoped
  rows with `revision` int, `parent_id` chain, `score_json` payload;
  quantize already writes revision 0). On a project's first edit, a
  baseline revision is bootstrapped from the current `assign` artifact
  (`created_by="baseline"`); every applied edit inserts the next
  revision. A NEW edit while rewound first deletes the revisions above
  the head (standard linear-history truncation), then inserts.
- Head pointer: `Project.settings["scoreHeadRevisionId"]` (the settings
  JSON column already exists — no migration). Key absent means "no
  edits, use the assign artifact", preserving today's behavior for
  untouched projects. `GET /v1/projects/{id}/score`, exports, and
  re-derive all resolve through the head pointer.
- "Revert to original" moves the head to the baseline revision.

### 4.3 Re-derivation (the slow half, decoupled)

- Re-derive runs on the existing in-process thread-pool queue as its
  own job function (aura-api stays decoupled from worker internals,
  same as transcription jobs). It is observable through the existing
  `GET /v1/jobs/{id}` by recording it as a `TranscriptionJob` row with
  `stage="rederive"` (no schema change). It re-runs the fingering/hand
  DP over the head score honoring `locked` events (locked notes keep
  their string/fret/hand; the DP optimizes around them), writes the
  re-assigned result back into the head revision's `score_json`
  (derived data, not user intent), then re-exports MusicXML + MIDI —
  the MIDI written from the score's events (`onsetSeconds`/
  `offsetSeconds`), since raw inference notes don't reflect edits —
  updating the project's existing `Export` rows in place (`revision`
  bumped, `object_key` swapped) so export ids stay stable for the
  frontend.
- Coalescing: at most one re-derive runs per project; a new edit while
  one is running marks it stale and re-enqueues once. The API applies
  edits synchronously regardless — only notation/export refresh waits.
- Failure: the version stands; the job records an error the frontend
  surfaces as "notation out of date — retry".

## 5. Frontend

### 5.1 Selection and hit-testing

- `Notation.svelte` maps a click to a score-JSON event id using the
  same cursor-walk correlation the playback timeline uses (non-rest
  step order + pitch + staff), preferring MusicXML `id` attribute
  pass-through if music21 supports writing it (verified during
  planning; correlation fallback otherwise). Selected note gets a
  visual highlight (amber, consistent with the cursor).
- Selection is kept by event id and restored after every re-render
  (zoom, tab toggle, post-edit refresh).

### 5.2 Inspector (Sidebar)

- New Inspector section when a note is selected: pitch (note-name
  spinner), onset/duration (grid-step steppers), voice (read-only v1),
  string/fret (guitar) or hand (piano), lock toggle, confidence
  readout. Delete button. "Add note" mini-form (pitch + duration,
  inserted at the selected position or measure start).
- Detection-facts section becomes editable: key (picker), tempo
  (number), meter (picker) → `set_part_fact`.
- Undo / redo / revert buttons with disabled states from the API's
  409 semantics; keyboard Ctrl+Z / Ctrl+Shift+Z.
- Keyboard on a selected note: ↑/↓ semitone (Shift = octave),
  ←/→ onset nudge one grid step, Delete removes.

### 5.3 Edit loop and latency

- An edit POST returns the new score JSON immediately → inspector and
  playback timeline update at once. Notation shows a subtle
  "updating…" state until the coalesced re-derive finishes and the
  refreshed MusicXML re-renders (frontend polls the re-derive job or
  refetches export after job completion; ~500ms quiet-period
  coalescing server-side).
- Playback keeps working on the edited JSON between renders (synth
  schedules from JSON; recording cursor uses recomputed onsetSeconds).

## 6. Error Handling

- 422 reasons render inline in the inspector next to the offending
  field; the edit is not applied.
- Re-derive failure: persistent, dismissible banner on the score view
  with a Retry action; score JSON remains authoritative.
- Undo/redo at bounds: buttons disabled; direct keyboard hits no-op.

## 7. Testing

- Python unit: every `apply_edit` op (happy + invalid), timeMap
  recomputation, validation round-trips.
- DP-lock tests: locked notes survive re-assign for guitar and piano.
- API tests: edit → version bump → head pointer; undo/redo/revert
  walk incl. truncate-on-new-edit and 409 bounds; 422 reasons.
- Frontend unit (Vitest): click→eventId correlation, selection
  restoration across re-render, inspector field validation, undo/redo
  button state.
- One e2e journey: transcribe → select → change pitch → notation
  updates → undo → export reflects head version.

## 8. Global Constraints (inherited, binding)

- Fixed port 8317; fully offline at runtime.
- `aura_api.main` untouched by CORS logic; no wildcard on `/v1/*`;
  existing routes keep their shapes (new endpoints only, plus the one
  `Project` migration).
- Visual language per the approved canvas (dark UI `#1e1d21`/`#26242a`,
  border `#37343c`, text `#e8e5df`/`#9b968c`, amber `#d99a4e`, paper
  `#f5f1e8`, system-ui).
- Third-party API names (OSMD internals) verified against installed
  typings, never memory.
- Tests: Vitest frontend; backend follows existing patterns with
  unconditional env overrides (never `setdefault`).
