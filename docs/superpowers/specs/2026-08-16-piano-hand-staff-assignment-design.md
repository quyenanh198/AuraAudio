# Piano Hand and Staff Assignment — Design

## Context

The transcription pipeline (Phase 1, plus Phase 2 sub-projects 1 and 2) now produces
a canonical score with correctly-pitched, correctly-timed notes, and — for guitar —
a playable `(string, fret)` per note. This sub-project (`ARCHITECTURE.md` §10 Phase 2,
item 2, piano half) does the analogous work for piano: assign each note to a hand
(left/right) so the exported MusicXML can render a real two-staff grand staff instead
of a single generic staff.

This is scoped as piano only, mirroring sub-project 2's guitar-only split. It reuses
the existing `assign` worker stage (currently a no-op passthrough for piano) rather
than adding a new pipeline stage — this sub-project fills in that stage's piano
branch.

## Goal

Given a quantized canonical score for a piano project, assign every note to a hand
(`"left"` or `"right"`) such that the resulting split:
- **respects hand span** — a hand's simultaneously-played notes stay within a
  playable interval where possible,
- **is locally coherent** — minimizes unnecessary hand-position movement across
  consecutive notes/chords,
- **treats middle C as a weak prior, not a hard boundary** — the split point drifts
  with the music rather than snapping to a fixed pitch,

and renders as a real two-staff piano grand staff (treble = right hand, bass = left
hand) in the exported MusicXML.

## Non-Goals (deferred)

- **Cross-staff notation.** Each hand is always notated on its own staff (right →
  treble, left → bass). `ARCHITECTURE.md` mentions allowing a note to be notated on
  the "wrong" staff when musically clearer; that's editor-facing polish deferred
  past this MVP, matching how sub-project 2 deferred alternate tunings and locked
  overrides.
- **True polyphonic voice separation.** Same simplification sub-project 1 established
  for guitar: single voice + chords (notes sharing an onset), not independent
  overlapping voices (e.g. a bass line under a melody with different onsets).
  `quantize` doesn't support real overlap handling yet — an inherited gap, not one
  this sub-project resolves.
- **User-locked hand overrides.** The schema's `locked` field exists per Phase 1's
  event shape but there's no editing UI yet — this stage always produces
  `locked: false`, matching sub-project 2's identical precedent.
- **Pedal detection and detailed fingering.** `ARCHITECTURE.md` §4.3 explicitly calls
  these post-MVP.
- **Configurable keyboard range.** Standard 88-key range (MIDI 21–108, A0–C8) is
  hardcoded. `ARCHITECTURE.md` mentions "the configured keyboard range," but no such
  configuration exists yet — this sub-project doesn't add one, matching how sub-project
  2 hardcoded standard guitar tuning.

## Architecture

The existing `assign` worker stage (added in sub-project 2, runs between `quantize`
and `export`) currently no-ops for `instrument == "piano"`. This sub-project
implements that branch: for each measure, group events by shared `notatedOnset`
(chords) exactly as sub-project 2's `_measure_groups` did, and assign each event a
`hand`. The guitar branch is untouched.

A new pure-algorithm module, `aura_worker.piano_hands`, structurally parallel to
`aura_worker.fingering`: candidate generation per onset, then a sequence DP across
the measure. No project dependencies beyond the standard library.

### Range check (independent of hand assignment)

`STANDARD_PIANO_RANGE = (21, 108)` (MIDI, A0–C8). A note outside this range gets
`hand: null` in the score JSON (data-quality signal, mirrors guitar's null-for-
unreachable pattern) — but see "MusicXML rendering" below for how it's still
notated, since piano export has no staff-less fallback the way guitar's single-staff
export does.

### Candidate split points

Unlike guitar frets, there is no "unreachable" case in the hand-split step itself —
any split of a chord's notes between two hands is physically possible. The model:
for an onset with distinct sorted pitches `p_1 < p_2 < ... < p_k` (`k >= 1`), a
candidate is a **split index** `i` in `0..k`: the lowest `i` pitches go to the left
hand, the remaining `k - i` go to the right hand. This gives `k + 1` candidates per
onset (all-right, all-left, and every point between). By construction, a hand-split
candidate can never produce interleaved/crossed hands *within* one onset — the left
hand's notes are always the lower ones.

Each candidate has a **boundary value**, the scalar used for DP transition cost
(directly analogous to a guitar chord's representative fret):
- `i == 0` (all right): `boundary = p_1 - 1`
- `i == k` (all left): `boundary = p_k + 1`
- otherwise: `boundary = (p_i + p_{i+1}) / 2`

### Sequence DP (Viterbi-style, same shape as sub-project 2's `assign_measure`)

Process each measure's onset groups in order. Transition cost between consecutive
candidates:

```
cost(prev, curr) = SPLIT_MOVEMENT_WEIGHT * abs(curr.boundary - prev.boundary)
                  + HAND_SPAN_PENALTY_WEIGHT * (
                        max(0, curr.left_span - PREFERRED_MAX_SPAN)
                      + max(0, curr.right_span - PREFERRED_MAX_SPAN)
                    )
                  + MIDDLE_C_PULL_WEIGHT * abs(curr.boundary - MIDDLE_C_MIDI)
```

with `SPLIT_MOVEMENT_WEIGHT = 1.0`, `HAND_SPAN_PENALTY_WEIGHT = 0.5`,
`PREFERRED_MAX_SPAN = 12` (one octave, semitones), `MIDDLE_C_PULL_WEIGHT = 0.05`
(deliberately small — a weak prior, not a hard boundary, per `ARCHITECTURE.md`),
`MIDDLE_C_MIDI = 60`. `left_span`/`right_span` are `p_i - p_1` / `p_k - p_{i+1}`
(0 if that hand has no notes this onset). Entry cost (first onset) uses the same
span and middle-C terms, no movement term. The DP keeps, for each candidate at
onset `i`, the minimum cumulative cost and a backpointer; final assignment
backtracks from the lowest-cost state at the last onset — same mechanics as
sub-project 2's `assign_measure`, including how a note gets `hand: null` (out of
piano range): it's excluded from the DP entirely, and the onsets before/after it
become adjacent for transition-cost purposes (no zero-cost bridging through it).

State count per onset is bounded by chord size (realistically well under a dozen
simultaneous notes), so this is trivially fast, same as guitar's DP.

### Canonical score schema (v4)

`schemaVersion` bumps `3` → `4`. Each event gains one optional field:

```json
{
  "id": "note_01",
  "pitch": 64,
  ...
  "hand": "right"
}
```

- `hand` — `"left"`, `"right"`, or `null` (unassigned/out-of-range, or the part
  isn't piano).
- Optional at the schema level (not required, nullable), same rationale as
  sub-project 2's `string`/`fret`: business logic enforces "piano parts get real
  values where possible," not JSON Schema conditionals.
- No migration tooling, same accepted-breaking-bump precedent as v1→v2 and v2→v3.

### MusicXML rendering

Piano gets a real structural change to `score_json_to_musicxml`, not just an
additive one (guitar only added `<technical>` articulations to an existing
single-staff note loop). Verified directly against real `music21` output before
writing this into the spec: two `stream.PartStaff` objects (one per hand, each with
its own clef — `TrebleClef` for right, `BassClef` for left), grouped into one
brace-joined system via `layout.StaffGroup([right, left], symbol="brace")`. This
produces one `<part>` with `<staves>2</staves>`, per-staff `<clef>` elements, and
each note tagged `<staff>1</staff>` (treble) or `<staff>2</staff>` (bass), with the
`<backup>` element between staves handled automatically by `music21` when both
`PartStaff`s' measures are populated per measure number.

Because a piano note must be notated on *some* staff to exist in the file at all
(unlike guitar, which always has a plain staff regardless of tab data), an
out-of-range note (`hand: null`) is still rendered — clamped to the nearer staff
for display purposes only (pitch below the range → bass/left staff, pitch above →
treble/right staff). The `hand: null` in the score JSON is preserved as the
authoritative "outside standard range" signal; the clamp is purely a rendering
fallback so no note is silently dropped from the exported file.

Non-piano (guitar) export path is completely untouched — this is a new branch in
`score_json_to_musicxml`, gated on `part["instrument"] == "piano"`.

## Testing

Deterministic, hand-verifiable cases (same style as sub-project 2 — pure
combinatorial logic, hand-picked note sequences):

- A wide two-hand passage (e.g. a low bass note plus a melody an octave-plus above)
  should split cleanly and stay split — tests that the DP doesn't oscillate hands
  unnecessarily (movement cost dominates).
- A chord spanning more than an octave should split between hands at the index
  minimizing combined span penalty, not always at a fixed point — tests the span
  penalty term directly.
- A borderline passage straddling middle C should follow continuity from the
  previous note's hand rather than snapping to a hand purely based on being above
  or below middle C — tests that `MIDDLE_C_PULL_WEIGHT` is genuinely weak.
- A note below MIDI 21 or above MIDI 108 gets `hand: null`, and the job still
  succeeds — tests the out-of-range path doesn't fail the stage, mirroring
  sub-project 2's equivalent test.
- Property test (matches `ARCHITECTURE.md` §9's stated target, and sub-project 2's
  precedent of committing this as a real test, not just an ad hoc check): for any
  onset, a chosen split index never produces a left-hand pitch greater than a
  right-hand pitch at that same onset (structural, but worth asserting directly).
- `instrument == "guitar"` regression check: existing guitar tests continue to
  pass unchanged — this sub-project's piano branch must not perturb the guitar path.
- MusicXML rendering: a piano score with mixed left/right notes exports with
  `<staves>2</staves>`, correct clef order (treble first, bass second, matching
  the verified prototype), and each note's `<staff>` element matching its `hand`
  (`1` for right, `2` for left) — asserted on the real exported XML string. An
  out-of-range note still appears in the file, clamped to the nearer staff.

## Error Handling

No new `JobErrorCode` values, same as sub-project 2. Per-note range exclusion
(`hand: null`) is data, not a failure — it never raises `JobFailure`. The stage can
only fail the way every other stage can: an unexpected exception surfaces as
`INTERNAL_ERROR` via `runner.py`'s existing catch-all.

## Definition of Done

- A developer can feed the pipeline a piano clip and see every note carry a `hand`
  value **in the exported MusicXML file** (not just the internal score JSON), with
  a real two-staff grand staff (verified clef, staff, and backup structure) — not
  just the internal `hand` field.
- Guitar projects are completely unaffected end-to-end (schema, `assign` stage,
  and MusicXML export all confirmed unchanged for `instrument == "guitar"`).
- `quantize`'s output feeding directly into `assign` and `assign`'s output feeding
  directly into `export` both round-trip through `validate_score` (v4) without
  manual glue.
- Full workspace test suite passes, including new coverage for the piano branch of
  `assign` and updated `score_schema`/`musicxml` v4 handling.
