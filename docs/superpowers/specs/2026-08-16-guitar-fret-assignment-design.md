# Guitar String and Fret Assignment — Design

## Context

The transcription pipeline (Phase 1, plus Phase 2 sub-project 1's beat/meter/key
detection) now produces a canonical score with correctly-pitched, correctly-timed
notes, but every note is just a MIDI pitch — nothing says which string and fret
plays it. This sub-project (`ARCHITECTURE.md` §10 Phase 2, item 2, guitar half
only) adds a worker stage that assigns each note a playable `(string, fret)`,
so the exported MusicXML can carry real tab notation instead of only staff
notation.

This is scoped as guitar only. Piano hand/staff assignment is a separate,
independent sub-project (different algorithm, different domain) — see
`docs/superpowers/SESSION-HANDOFF.md` for the full Phase 2 sub-project list.

## Goal

Given a quantized canonical score for a guitar project, assign every note a
`(string, fret)` pair that is:
- **playable** — reachable in standard tuning within a fixed fret range,
- **locally coherent** — minimizes fret movement and unnecessary string
  changes across the sequence,
- **hand-friendly** — prefers lower, more accessible frets and narrower
  chord shapes,

while never assigning two simultaneous notes (a chord) to the same string.

## Non-Goals (deferred)

- **Alternate tunings.** Standard EADGBE only. The `Project.tuning` field
  (present but unused since Phase 1) is wired up as a fixed `"standard"`
  default now; alternate tunings are a later pass — the open-string-pitch
  table is the only thing that would need to change, the algorithm doesn't.
- **Piano hand/staff assignment.** Separate sub-project.
- **Techniques** (bends, slides, hammer-ons, pull-offs, harmonics, capo
  inference) — `ARCHITECTURE.md` §4.3 already calls these post-MVP because
  pitch alone doesn't identify them.
- **User-locked string/fret overrides.** The schema's `locked` field exists
  per Phase 1's event shape but there is no editing UI yet (Phase 3+) to set
  it — this stage always produces `locked: false`, matching how Phase 2
  sub-project 1 handled the same not-yet-built-UI situation.
- **True multi-voice / general overlap handling.** The "distinct string per
  chord" hard constraint below only applies to notes sharing the *same*
  onset (a true chord). Notes that overlap in time without sharing an onset
  are an inherited, already-documented gap from sub-project 1 (`quantize`
  always uses voice 1, no overlap trimming) — not something this
  sub-project resolves.
- **"Next fingering" alternatives.** `ARCHITECTURE.md` mentions persisting
  alternative candidates so an editor can offer a different fingering later.
  No editor exists yet; only the chosen assignment is persisted. Revisit
  when Phase 3 needs it.

## Architecture

A new worker stage, `assign`, runs after `quantize` and before `export`:

```
probe -> normalize -> inference -> structure -> quantize -> assign -> export
```

For `instrument == "guitar"`, `assign` walks every measure's events, groups
simultaneous notes (same `notatedOnset` within a measure) into chords, and
assigns each note a `(string, fret)`. For `instrument == "piano"`, the stage
is a no-op passthrough — it returns the score unchanged (still runs, for
pipeline-shape consistency, but does nothing).

### Candidate generation

Standard tuning open-string MIDI pitches (string 0 = low E through string 5
= high E): `[40, 45, 50, 55, 59, 64]`. `MAX_FRET = 20`.

For a pitch `p`, string `s` is a valid candidate iff
`0 <= p - OPEN_STRING_PITCH[s] <= MAX_FRET`, giving `fret = p - OPEN_STRING_PITCH[s]`.

A pitch with **zero** valid candidates (out of the guitar's playable range
entirely — e.g. below the open low E) is left unassigned (`string`/`fret`
stay `null`) rather than failing the stage. This is a per-note data-quality
outcome, not a stage failure — consistent with the project's "assisted
transcription, not perfect" framing (`ARCHITECTURE.md` §1).

### Single-note sequence: DP (Viterbi-style)

Process each measure's events in onset order. For a monophonic run (no
chords), this is a standard shortest-path DP over candidate `(string,
fret)` states per note, with transition cost between consecutive notes:

```
cost(prev, curr) = FRET_MOVE_WEIGHT * abs(curr.fret - prev.fret)
                  + (STRING_CHANGE_PENALTY if curr.string != prev.string else 0)
                  + RANGE_PENALTY_WEIGHT * max(0, curr.fret - PREFERRED_MAX_FRET)
```

with `FRET_MOVE_WEIGHT = 1.0`, `STRING_CHANGE_PENALTY = 2.0`,
`PREFERRED_MAX_FRET = 12`, `RANGE_PENALTY_WEIGHT = 0.5`. The DP keeps, for
each candidate state at note `i`, the minimum cumulative cost and a
backpointer; the final assignment backtracks from the lowest-cost state at
the last note. State count per note is bounded by the string count (≤6), so
this is trivially fast regardless of clip length — no scalability concern.

A note with zero candidates breaks the chain: it's assigned `null` and
excluded from the DP: the note *before* it and the note *after* it become
adjacent for transition-cost purposes (skip, don't zero-cost-bridge through
a null state).

### Chords: bipartite assignment, then DP

For a set of `k` simultaneous pitches (a chord), first assign each pitch to
a **distinct** string:

1. Build each pitch's candidate string list (as above).
2. Search assignments (small `k`, bounded by 6 strings — brute-force over
   permutations of candidate strings is fine; most pitches have only 1-3
   valid string options in practice, so the real search space is far
   smaller than `6!`).
3. Among assignments that give every pitch a distinct string, pick the one
   minimizing **hand stretch**: `max(fret) - min(fret)` across the chord's
   assigned strings. This is the hard constraint (never two notes on one
   string) plus one heuristic (narrowest stretch wins) — it does not
   attempt real barre-shape detection, matching the Non-Goals framing:
   narrower stretch is a reasonable proxy for "easier to play" without
   needing fingering pedagogy data.
4. If no assignment gives *every* pitch a distinct string (more pitches
   than reachable strings, or a pitch has zero candidates at all), fall
   back to a **maximum** (not perfect) bipartite matching: assign as many
   pitches as possible to distinct strings, leave the rest `null`. Never
   drop the hard constraint to force a full assignment.

The resulting chord — a set of `(string, fret)` pairs — becomes one
"state" in the sequence-level DP, using the same transition-cost formula
above (fret movement and string-change cost computed against the closest
prior/next single-note or chord state; for a chord-to-chord or
note-to-chord transition, use the assigned note/string pair that's
adjacent in pitch, or the chord's lowest string if ambiguous — exact tie-
break is an implementation detail the plan can settle, not a hard
requirement).

### Canonical score schema (v3)

`schemaVersion` bumps `2` → `3`. Each event gains two optional fields:

```json
{
  "id": "note_01",
  "pitch": 64,
  ...
  "string": 2,
  "fret": 5
}
```

- `string` — integer `0`-`5` (0 = low E) or `null` if unassigned or the part
  isn't a guitar.
- `fret` — integer `0`-`20` or `null` under the same conditions.
- Both fields are **optional** at the schema level (not required, nullable)
  rather than conditionally required per instrument — simpler schema,
  business logic (not JSON Schema) enforces "guitar parts get real values
  where possible." This avoids `if`/`then` schema conditionals for a
  distinction that's easy to express in code and easy to unit-test.
- As with the v1→v2 bump, no migration tooling — no production data exists
  yet, this is an accepted breaking bump.

### MusicXML rendering

Without this, the assigned `(string, fret)` data is computed and stored in
the score JSON but never reaches the actual exported file a musician
opens — `ARCHITECTURE.md` §4.4 explicitly requires "technical string/fret
elements" in the MusicXML output, so this is in scope, not a follow-up.

`music21` renders tab notation via `note.Note.articulations`, not a
constructor argument — verified directly against real output before writing
this into the spec: `n.articulations.append(articulations.StringIndication(k))`
and `n.articulations.append(articulations.FretIndication(f))` together
produce `<notations><technical><string>k</string><fret>f</fret></technical></notations>`
on that note.

**Numbering convention mismatch, verified and must be converted:** this
spec's internal `string` field is 0-indexed low-to-high (0 = low E, 5 =
high E) because that matches the open-string-pitch array's natural order.
MusicXML's `<string>` element for fretted instruments uses the standard tab
convention — 1-indexed **high-to-low** (1 = high E, 6 = low E). `musicxml/
export.py` must convert: `musicxml_string = 6 - internal_string`. Get this
backwards and every fingering silently displays on the mirror-image string
with no validation error to catch it (MusicXML accepts any 1-6 integer).

`musicxml/export.py` only appends the `StringIndication`/`FretIndication`
articulations when both `event["string"]` and `event["fret"]` are non-null
(piano parts, or unassigned guitar notes, get no `<technical>` block at
all — omission, not a zero/null element).

## Testing

Deterministic, hand-verifiable cases (no DSP/audio involved this time — this
is pure combinatorial logic, testable with hand-picked note sequences, not
synthetic audio fixtures):

- A descending/ascending chromatic run should mostly stay on one string
  (fret movement is cheaper than repeated string changes for adjacent
  semitones on the same string).
- An open-position major triad (e.g. E-A-D low strings, open or near-open
  frets) should resolve to low frets, not high ones — tests the range
  penalty.
- A chord requiring 4+ distinct pitches should never produce two notes on
  the same string — the hard constraint, tested directly.
- A pitch below the guitar's range (e.g. MIDI 30) gets `string: null, fret:
  null`, and the job still succeeds — tests the zero-candidate path doesn't
  fail the stage.
- Property test (matches `ARCHITECTURE.md` §9's stated target): for any
  randomly generated set of simultaneous pitches within guitar range and
  count ≤ 6, the assigned strings (excluding `null`s) are always distinct.
- `instrument == "piano"` passthrough: score is unchanged (no `string`/
  `fret` keys added, or present as `null` — whichever the schema settles
  on) and the stage doesn't error.
- MusicXML rendering: a note with `string: 2, fret: 5` (internal, 0-indexed
  low-to-high) exports as `<string>4</string><fret>5</fret>` (MusicXML,
  1-indexed high-to-low: `6 - 2 = 4`) — asserted on the real exported XML
  string, not just "didn't crash." A note with `string: null` produces no
  `<technical>` block at all.

## Error Handling

No new `JobErrorCode` values. Per-note unassignability (impossible pitch,
partial chord match) is data, not a failure — it never raises `JobFailure`.
The stage can only fail the way every other stage can: an unexpected
exception surfaces as `INTERNAL_ERROR` via `runner.py`'s existing catch-all.

## Definition of Done

- A developer can feed the pipeline a guitar clip and see every reachable
  note carry a valid `(string, fret)` **in the exported MusicXML file**
  (not just the internal score JSON), with the hard constraint (distinct
  strings within a chord) provably never violated, and correct MusicXML
  string-numbering conversion.
- Piano projects pass through `assign` unchanged, and their MusicXML export
  is unaffected (no `<technical>` blocks).
- `quantize`'s output feeding directly into `assign` and `assign`'s output
  feeding directly into `export` both round-trip through `validate_score`
  (v3) without manual glue.
- Full workspace test suite passes, including new coverage for `assign` and
  updated `score_schema`/`musicxml` v3 handling.
