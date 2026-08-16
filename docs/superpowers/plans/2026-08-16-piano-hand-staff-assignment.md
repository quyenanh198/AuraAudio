# Piano Hand and Staff Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assign every piano note to a hand (`"left"`/`"right"`) via a deterministic split-point optimizer, and render it as a real two-staff grand staff in the exported MusicXML.

**Architecture:** The existing `assign` worker stage (added in sub-project 2, runs between `quantize` and `export`) currently no-ops for `instrument == "piano"` — this plan fills in that branch, so **no new pipeline stage or runner wiring is needed**. Pure algorithmic logic (per-onset candidate splits, sequence DP) lives in a new `aura_worker.piano_hands` module, independently unit-testable, structurally parallel to sub-project 2's `aura_worker.fingering`. The canonical score schema bumps to v4 (optional `hand` per event). `musicxml/export.py` gains a real structural branch for piano: two `music21.stream.PartStaff` objects grouped into one grand staff via `layout.StaffGroup`.

**Tech Stack:** Pure Python (no new dependencies) for the algorithm; `music21`'s `stream.PartStaff` + `layout.StaffGroup` (verified directly against real output before this plan was written) for MusicXML rendering.

**Spec:** `docs/superpowers/specs/2026-08-16-piano-hand-staff-assignment-design.md`

## Global Constraints

- Standard 88-key piano range only: `STANDARD_PIANO_RANGE = (21, 108)` (MIDI, A0-C8, inclusive). A pitch outside this range gets `hand: null` in the score JSON.
- Unlike guitar frets, there is **no hard "unreachable" case in the hand-split step itself** — any split of a chord's pitches between two hands is physically possible. The range check is a separate, independent filter.
- Weights (verified via direct execution, not hand-derived): `SPLIT_MOVEMENT_WEIGHT = 1.0`, `HAND_SPAN_PENALTY_WEIGHT = 0.5`, `PREFERRED_MAX_SPAN = 12` (semitones), `MIDDLE_C_PULL_WEIGHT = 0.05`, `MIDDLE_C_MIDI = 60`.
- No new `JobErrorCode` values. Out-of-range exclusion is data, not a failure.
- `instrument == "guitar"` is completely unaffected by this plan — its branch in `assign.py` is untouched, and `export.py`'s single-staff path is untouched.
- `schemaVersion` bumps `3` → `4` as an accepted breaking change (no migration tooling, same reasoning as prior bumps).
- `hand` is optional (not required) on each event in the JSON Schema — mirrors how `string`/`fret` were added in sub-project 2.
- MusicXML rendering (verified directly against real `music21` output before this plan was written):
  - `stream.PartStaff` (not plain `stream.Part`) for each hand, grouped via `layout.StaffGroup([right, left], symbol="brace")`, produces **one** `<part>` with `<staves>2</staves>` — not two separate parts.
  - `TimeSignature`/`Key`/`instrument.Piano()` inserted on **both** `PartStaff`s at index 0 — `music21` deduplicates them into one shared `<attributes>` block per measure; this is safe, not redundant output.
  - The tempo mark (`tempo.MetronomeMark`) only needs to be inserted into the **right-hand staff's first measure** — it renders once, correctly, not duplicated.
  - A measure where one hand has no notes needs no manual rest insertion — `music21` fills a full-measure rest automatically on write, and `music21.converter.parse(...).flatten().notes` correctly excludes those rests when reopened (verified: `reopen_and_check`'s existing `expected_note_count` logic, which counts total events across all measures regardless of hand, needs **no changes** for piano).
  - Right hand → treble clef → staff 1. Left hand → bass clef → staff 2. This is fixed by insertion order (`right` first, `left` second) into both the `StaffGroup` list and the `Score`.
  - `packages/musicxml` cannot depend on `workers/transcription` (packages are lower-layer than workers) — `STANDARD_PIANO_RANGE` must be duplicated as a private constant in `export.py`, mirroring how sub-project 2 inlined its `6 - internal_string` conversion rather than importing `aura_worker.fingering`'s constants.
  - An out-of-range note (`hand: null` in the score JSON) is still rendered — clamped to the nearer staff purely for display (pitch below `STANDARD_PIANO_RANGE[0]` → left/bass staff, otherwise → right/treble staff). The `null` in the JSON remains the authoritative "outside standard range" signal; the clamp never touches the JSON.

## File Structure

```text
packages/score_schema/src/score_schema/
  models.py         # Modify: schemaVersion 3 -> 4 in build_score()
  validate.py        # Modify: v4 schema — hand optional string-or-null on events
packages/score_schema/tests/
  test_models.py      # Modify: schemaVersion assertion
  test_validate.py     # Modify: v4 fixtures, hand coverage

workers/transcription/src/aura_worker/
  piano_hands.py      # Create: pure algorithm — candidate splits, sequence DP
  stages/
    assign.py          # Modify: fill in piano branch (was no-op passthrough)
workers/transcription/tests/
  test_piano_hands.py   # Create
  test_assign.py       # Modify: piano tests now assert real hand values; guitar
                        # monkeypatch test's patch target renamed

packages/musicxml/src/musicxml/
  export.py         # Modify: piano branch renders two-staff grand staff
packages/musicxml/tests/
  test_export.py      # Modify: new assertions for grand-staff rendering

apps/api/tests/
  test_e2e_pipeline.py # Modify: add a piano e2e test asserting <staves>2</staves>
                        # reaches the real exported file (closing the same kind
                        # of seam-coverage gap sub-project 2's final review found)
```

---

## Task 1: `score_schema` v4 — optional `hand` field

**Files:**
- Modify: `packages/score_schema/src/score_schema/models.py`
- Modify: `packages/score_schema/src/score_schema/validate.py`
- Modify: `packages/score_schema/tests/test_models.py`
- Modify: `packages/score_schema/tests/test_validate.py`

**Interfaces:**
- Produces: `build_score(...)` now stamps `schemaVersion: 4` (no parameter changes — `hand` is a per-event field set later by the `assign` stage). `validate_score` accepts `schemaVersion: 4`, and permits (without requiring) `hand` on each event as `"left"`, `"right"`, or `null`.

- [ ] **Step 1: Update the failing test for schemaVersion 4**

In `packages/score_schema/tests/test_models.py`, rename `test_build_score_produces_schema_v3_shape` to `test_build_score_produces_schema_v4_shape` and change its assertion:

```python
    assert score["schemaVersion"] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_models.py -v`
Expected: FAIL — `assert 3 == 4`

- [ ] **Step 3: Bump the constant in `models.py`**

In `build_score`'s return dict, change `"schemaVersion": 3` to `"schemaVersion": 4`. Also update the docstring's `schemaVersion-3` reference to `schemaVersion-4`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing tests for v4 validation**

Append to `packages/score_schema/tests/test_validate.py`:

```python
def test_schema_v3_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 3
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_event_without_hand_is_accepted():
    validate_score(_valid_score())  # _valid_score()'s event has no hand key at all


def test_event_with_null_hand_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["hand"] = None
    validate_score(score)  # must not raise


def test_event_with_valid_hand_values_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["hand"] = "left"
    validate_score(score)  # must not raise
    score["parts"][0]["measures"][0]["events"][0]["hand"] = "right"
    validate_score(score)  # must not raise


def test_event_with_invalid_hand_value_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["hand"] = "both"
    with pytest.raises(ScoreValidationError):
        validate_score(score)
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_validate.py -v`
Expected: FAIL — `test_schema_v3_is_rejected` fails because `schemaVersion` is still const `3`; `test_event_with_valid_hand_values_is_accepted`/`test_event_with_null_hand_is_accepted` fail because `additionalProperties: False` currently rejects `hand` as an unrecognized key.

- [ ] **Step 7: Update `_EVENT_SCHEMA` and `_SCORE_SCHEMA` in `validate.py`**

Add one property to `_EVENT_SCHEMA["properties"]` (do not add `hand` to `required`):

```python
        "hand": {"enum": ["left", "right", None]},
```

Change `_SCORE_SCHEMA["properties"]["schemaVersion"]` from `{"const": 3}` to `{"const": 4}`. Everything else is unchanged.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests -v`
Expected: PASS (all tests — prior 17 plus 5 new = 22)

- [ ] **Step 9: Commit**

```bash
git add packages/score_schema/src/score_schema/models.py packages/score_schema/src/score_schema/validate.py packages/score_schema/tests/test_models.py packages/score_schema/tests/test_validate.py
git commit -m "feat(score-schema): bump canonical score to v4 with optional hand field"
```

---

## Task 2: `aura_worker.piano_hands` — candidate split generation

**Files:**
- Create: `workers/transcription/src/aura_worker/piano_hands.py`
- Create: `workers/transcription/tests/test_piano_hands.py`

**Interfaces:**
- Produces: `HandSplit(boundary: float, left: tuple[int, ...], right: tuple[int, ...])` frozen dataclass; `candidate_splits(pitches: list[int]) -> list[HandSplit]` — for `k` distinct-or-not pitches sharing one onset, returns `k + 1` candidates (split index `0..k`, lowest `i` pitches to `left`, rest to `right`), each carrying a scalar `boundary` used for DP transition cost. `STANDARD_PIANO_RANGE = (21, 108)` module constant.
- Consumes: nothing (pure logic, no project imports).

- [ ] **Step 1: Write the failing test for candidate splits**

```python
# workers/transcription/tests/test_piano_hands.py
from aura_worker.piano_hands import HandSplit, candidate_splits


def test_candidate_splits_single_pitch_gives_two_options():
    result = candidate_splits([60])
    assert len(result) == 2
    assert HandSplit(boundary=59, left=(), right=(60,)) in result
    assert HandSplit(boundary=61, left=(60,), right=()) in result


def test_candidate_splits_two_pitches_gives_three_options():
    result = candidate_splits([60, 72])
    assert len(result) == 3
    assert HandSplit(boundary=59, left=(), right=(60, 72)) in result
    assert HandSplit(boundary=66, left=(60,), right=(72,)) in result
    assert HandSplit(boundary=73, left=(60, 72), right=()) in result


def test_candidate_splits_sorts_unsorted_input():
    # verified directly: candidate_splits([72, 48, 60]) sorts internally to
    # [48, 60, 72] before splitting, regardless of input order
    result = candidate_splits([72, 48, 60])
    assert HandSplit(boundary=54, left=(48,), right=(60, 72)) in result


def test_candidate_splits_handles_duplicate_pitches():
    # two notes at the same pitch must not crash — position-based splitting
    # (not pitch-value matching) is required for this to behave sanely,
    # verified directly: this still produces exactly 3 candidates
    result = candidate_splits([60, 60])
    assert len(result) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_piano_hands.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aura_worker.piano_hands'`

- [ ] **Step 3: Write `candidate_splits` and supporting constants**

```python
# workers/transcription/src/aura_worker/piano_hands.py
from __future__ import annotations

from dataclasses import dataclass

STANDARD_PIANO_RANGE = (21, 108)  # MIDI A0 (21) .. C8 (108), inclusive

SPLIT_MOVEMENT_WEIGHT = 1.0
HAND_SPAN_PENALTY_WEIGHT = 0.5
PREFERRED_MAX_SPAN = 12
MIDDLE_C_PULL_WEIGHT = 0.05
MIDDLE_C_MIDI = 60


@dataclass(frozen=True)
class HandSplit:
    boundary: float
    left: tuple[int, ...]
    right: tuple[int, ...]


def candidate_splits(pitches: list[int]) -> list[HandSplit]:
    """pitches: the MIDI pitches sharing one onset (already known to be
    within STANDARD_PIANO_RANGE — that filtering happens one layer up, in
    assign_measure). Every split of the sorted pitches into a lower
    (left-hand) group and an upper (right-hand) group is a valid candidate —
    unlike guitar frets, there is no "unreachable" case here."""
    sorted_pitches = sorted(pitches)
    k = len(sorted_pitches)
    result = []
    for i in range(k + 1):
        left = tuple(sorted_pitches[:i])
        right = tuple(sorted_pitches[i:])
        if i == 0:
            boundary = sorted_pitches[0] - 1
        elif i == k:
            boundary = sorted_pitches[-1] + 1
        else:
            boundary = (sorted_pitches[i - 1] + sorted_pitches[i]) / 2
        result.append(HandSplit(boundary=boundary, left=left, right=right))
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_piano_hands.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add workers/transcription/src/aura_worker/piano_hands.py workers/transcription/tests/test_piano_hands.py
git commit -m "feat(worker): add piano_hands module — candidate split generation"
```

---

## Task 3: `aura_worker.piano_hands` — sequence DP over a measure

**Files:**
- Modify: `workers/transcription/src/aura_worker/piano_hands.py`
- Modify: `workers/transcription/tests/test_piano_hands.py`

**Interfaces:**
- Produces: `assign_measure(events: list[dict]) -> dict[int, str]` — takes a measure's `events` list (each a canonical-score event dict with at least `"pitch"` and `"notatedOnset"`), groups simultaneous notes by shared `notatedOnset`, and returns a mapping from event index to `"left"` or `"right"`. Indices not present in the returned dict are out of `STANDARD_PIANO_RANGE` (caller treats a missing index as `hand: null`).
- Consumes: `HandSplit`, `candidate_splits`, `STANDARD_PIANO_RANGE` (Task 2, same module).

- [ ] **Step 1: Write the failing tests for the sequence DP**

Append to `workers/transcription/tests/test_piano_hands.py`:

```python
from aura_worker.piano_hands import assign_measure


def _event(pitch: int, onset: str) -> dict:
    return {"pitch": pitch, "notatedOnset": onset}


def test_assign_measure_wide_two_hand_passage_stays_split():
    # A low bass pair followed by a high melody pair — verified directly:
    # movement cost (weight 1.0) dominates the tiny middle-C pull (0.05),
    # so each pair stays on its natural hand rather than oscillating.
    events = [
        _event(40, "0/1"), _event(43, "1/4"),
        _event(76, "1/2"), _event(79, "3/4"),
    ]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 1: "left", 2: "right", 3: "right"}


def test_assign_measure_wide_chord_splits_at_span_minimizing_index():
    # A chord spanning two octaves (48, 60, 72) — verified directly: the
    # split that puts 48 alone on the left and 60+72 together on the right
    # minimizes combined span penalty (both hands land at exactly the
    # PREFERRED_MAX_SPAN boundary, zero penalty) and wins the tie over the
    # symmetric alternative (48+60 left, 72 right) via first-found order.
    events = [_event(48, "0/1"), _event(60, "0/1"), _event(72, "0/1")]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 1: "right", 2: "right"}


def test_assign_measure_middle_c_is_a_weak_prior_not_a_hard_boundary():
    # A run starting clearly left-hand (50), then two notes straddling
    # middle C (58, 62) — verified directly: continuity keeps 58 on the
    # left hand even though it's below middle C's exact value, only
    # flipping to right at 62. A hard middle-C boundary would have split
    # right at 60 regardless of what came before; this doesn't.
    events = [_event(50, "0/1"), _event(58, "1/4"), _event(62, "1/2")]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 1: "left", 2: "right"}


def test_assign_measure_skips_out_of_range_pitch():
    # A pitch below MIDI 21 contributes no state and must not break the
    # chain between the notes before and after it.
    events = [_event(50, "0/1"), _event(10, "1/4"), _event(55, "1/2")]
    assignment = assign_measure(events)
    assert assignment == {0: "left", 2: "right"}


def test_assign_measure_handles_duplicate_pitch_chord():
    # Two notes at the same pitch sharing an onset — position-based
    # splitting (not pitch-value matching) means this must not crash and
    # must still produce one "left" and one "right" (verified directly).
    events = [_event(60, "0/1"), _event(60, "0/1")]
    assignment = assign_measure(events)
    assert set(assignment.values()) == {"left", "right"}
    assert len(assignment) == 2


def test_assign_measure_left_never_exceeds_right_within_an_onset_property():
    # Spec Testing bullet 5 (matches ARCHITECTURE.md §9's property-testing
    # target, and sub-project 2's precedent of committing this directly
    # rather than only checking it ad hoc): for any onset, every assigned
    # left-hand pitch is <= every assigned right-hand pitch. Seeded for
    # reproducibility, matching sub-project 2's fix-wave property test style.
    import random

    rng = random.Random(42)
    for _ in range(500):
        chord_size = rng.randint(1, 8)
        pitches = [rng.randint(21, 108) for _ in range(chord_size)]
        events = [_event(p, "0/1") for p in pitches]
        assignment = assign_measure(events)
        left_pitches = [pitches[i] for i, hand in assignment.items() if hand == "left"]
        right_pitches = [pitches[i] for i, hand in assignment.items() if hand == "right"]
        if left_pitches and right_pitches:
            assert max(left_pitches) <= min(right_pitches)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_piano_hands.py -v`
Expected: FAIL — `ImportError: cannot import name 'assign_measure'`

- [ ] **Step 3: Write `assign_measure` and its helpers**

Append to `workers/transcription/src/aura_worker/piano_hands.py`:

```python
def _in_range(pitch: int) -> bool:
    return STANDARD_PIANO_RANGE[0] <= pitch <= STANDARD_PIANO_RANGE[1]


@dataclass
class _PlacementOption:
    split: HandSplit
    left_indices: list[int]
    right_indices: list[int]


def _span_penalty(split: HandSplit) -> float:
    left_span = (split.left[-1] - split.left[0]) if len(split.left) > 1 else 0
    right_span = (split.right[-1] - split.right[0]) if len(split.right) > 1 else 0
    return max(0, left_span - PREFERRED_MAX_SPAN) + max(0, right_span - PREFERRED_MAX_SPAN)


def _transition_cost(prev: HandSplit, curr: HandSplit) -> float:
    cost = SPLIT_MOVEMENT_WEIGHT * abs(curr.boundary - prev.boundary)
    cost += HAND_SPAN_PENALTY_WEIGHT * _span_penalty(curr)
    cost += MIDDLE_C_PULL_WEIGHT * abs(curr.boundary - MIDDLE_C_MIDI)
    return cost


def _entry_cost(split: HandSplit) -> float:
    cost = HAND_SPAN_PENALTY_WEIGHT * _span_penalty(split)
    cost += MIDDLE_C_PULL_WEIGHT * abs(split.boundary - MIDDLE_C_MIDI)
    return cost


def _group_by_onset(events: list[dict]) -> list[list[int]]:
    """Group event indices by shared notatedOnset (a chord grouping),
    preserving first-seen order."""
    groups: dict[str, list[int]] = {}
    order: list[str] = []
    for i, ev in enumerate(events):
        onset = ev["notatedOnset"]
        if onset not in groups:
            groups[onset] = []
            order.append(onset)
        groups[onset].append(i)
    return [groups[o] for o in order]


def _options_for_group(events: list[dict], indices: list[int]) -> list[_PlacementOption]:
    in_range = [i for i in indices if _in_range(events[i]["pitch"])]
    if not in_range:
        return []
    # Sort by pitch (stable, so duplicate pitches keep their original
    # relative order) — splitting by POSITION in this sorted order, not by
    # matching pitch VALUES back to candidate_splits' left/right tuples,
    # is what makes duplicate-pitch chords split correctly.
    order = sorted(in_range, key=lambda i: events[i]["pitch"])
    pitches = [events[i]["pitch"] for i in order]
    splits = candidate_splits(pitches)
    options = []
    for k, split in enumerate(splits):
        options.append(_PlacementOption(split=split, left_indices=order[:k], right_indices=order[k:]))
    return options


def assign_measure(events: list[dict]) -> dict[int, str]:
    groups = _group_by_onset(events)
    all_steps = [_options_for_group(events, idxs) for idxs in groups]
    steps = [s for s in all_steps if s]  # drop wholly-out-of-range groups

    result: dict[int, str] = {}
    if not steps:
        return result

    # dp[i] = list of (cumulative_cost, backpointer_index_into_dp[i-1]) per option in steps[i]
    dp: list[list[tuple[float, int]]] = []
    for i, options in enumerate(steps):
        row: list[tuple[float, int]] = []
        if i == 0:
            for opt in options:
                row.append((_entry_cost(opt.split), -1))
        else:
            prev_options = steps[i - 1]
            prev_row = dp[i - 1]
            for opt in options:
                best_cost = None
                best_j = -1
                for j, prev_opt in enumerate(prev_options):
                    cost = prev_row[j][0] + _transition_cost(prev_opt.split, opt.split)
                    if best_cost is None or cost < best_cost:
                        best_cost = cost
                        best_j = j
                row.append((best_cost, best_j))
        dp.append(row)

    last_row = dp[-1]
    best_final = min(range(len(last_row)), key=lambda k: last_row[k][0])

    chosen = [0] * len(steps)
    idx = best_final
    for i in range(len(steps) - 1, -1, -1):
        chosen[i] = idx
        idx = dp[i][idx][1]

    for i, options in enumerate(steps):
        opt = options[chosen[i]]
        for j in opt.left_indices:
            result[j] = "left"
        for j in opt.right_indices:
            result[j] = "right"

    return result
```

`dataclass` is already imported at the top of the file from Task 2 (`from dataclasses import dataclass`) — do not add a second import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_piano_hands.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add workers/transcription/src/aura_worker/piano_hands.py workers/transcription/tests/test_piano_hands.py
git commit -m "feat(worker): add sequence DP for per-measure hand assignment"
```

---

## Task 4: `assign` worker stage — piano branch

**Files:**
- Modify: `workers/transcription/src/aura_worker/stages/assign.py`
- Modify: `workers/transcription/tests/test_assign.py`

**Interfaces:**
- Consumes: `assign_measure` from `aura_worker.piano_hands` (Task 3), aliased `assign_hands` to avoid colliding with `aura_worker.fingering.assign_measure` (already imported in this file, aliased `assign_string_fret`).
- Produces: `stages.assign.run(ctx: StageContext, score: dict) -> dict` unchanged signature, now also sets `event["hand"]` on every event — a real value for piano (or `null` if out of range), `null` for guitar (mirroring how guitar events always get `string`/`fret` keys, `null` for piano).

- [ ] **Step 1: Read the current file, then replace it in full**

Read `workers/transcription/src/aura_worker/stages/assign.py` first to confirm its current content matches what's described below (it was last touched by sub-project 2 — confirm before editing, since this task changes both the guitar and piano code paths together).

Replace the full file:

```python
# workers/transcription/src/aura_worker/stages/assign.py
from __future__ import annotations

import hashlib
import json

from aura_worker.fingering import assign_measure as assign_string_fret
from aura_worker.piano_hands import assign_measure as assign_hands
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.validate import validate_score

STAGE_VERSION = 2


def run(ctx: StageContext, score: dict) -> dict:
    cached = find_cached_artifact(ctx, "assign", STAGE_VERSION)
    if cached is not None:
        return json.loads(ctx.storage.get_bytes(cached.object_key))

    part = score["parts"][0]
    instrument = part["instrument"]
    for measure in part["measures"]:
        events = measure["events"]

        string_fret_assignments = assign_string_fret(events) if instrument == "guitar" else {}
        hand_assignments = assign_hands(events) if instrument == "piano" else {}

        for i, event in enumerate(events):
            sf = string_fret_assignments.get(i)
            event["string"] = sf.string if sf is not None else None
            event["fret"] = sf.fret if sf is not None else None
            event["hand"] = hand_assignments.get(i)

    validate_score(score)

    object_key = f"jobs/{ctx.job.id}/stage/assign.json"
    payload = json.dumps(score).encode()
    ctx.storage.put_bytes(object_key, payload)
    save_artifact(
        ctx, "assign", STAGE_VERSION, object_key=object_key,
        sha256=hashlib.sha256(payload).hexdigest(), metrics={},
    )
    return score
```

`STAGE_VERSION` bumps `1` → `2` — the stage's behavior changed (piano is no longer a no-op), matching the codebase's existing convention of bumping a stage's version when its output for a previously-passthrough case changes (same reasoning as `quantize`'s version bump in sub-project 1's Task 4).

- [ ] **Step 2: Update the existing guitar monkeypatch test's patch target**

In `workers/transcription/tests/test_assign.py`, the existing `test_assign_stage_second_call_resumes_without_recompute` monkeypatches the guitar string/fret function — its patch target's name changed in Step 1 above (`assign_measure` is now imported aliased as `assign_string_fret`). Update:

```python
    # assign.py does `from aura_worker.fingering import assign_measure as
    # assign_string_fret`, which binds the aliased name into assign.py's own
    # module namespace at import time — patching aura_worker.fingering's
    # original name afterward would NOT affect this already-bound reference.
    # Patch it where assign.py actually looks it up:
    # aura_worker.stages.assign.assign_string_fret.
    monkeypatch.setattr(assign, "assign_string_fret", fail_if_called)
```

(replacing the old `monkeypatch.setattr(assign, "assign_measure", fail_if_called)` line and its old comment.)

- [ ] **Step 3: Add a `hand: None` assertion to the existing guitar test**

In `test_assign_stage_sets_string_and_fret_for_guitar`, after the existing `assert event["fret"] is not None` line, add:

```python
    assert event["hand"] is None
```

- [ ] **Step 4: Replace the piano passthrough test with real hand-assignment tests**

Replace `_piano_score()` and `test_assign_stage_piano_passthrough` (the piano branch is no longer a no-op) with:

```python
def _piano_score_two_hands():
    return build_score(
        instrument="piano",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [
                {
                    "id": "note_00", "pitch": 40, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
                {
                    "id": "note_01", "pitch": 76, "onsetSeconds": 0.5, "offsetSeconds": 1.0,
                    "notatedOnset": "1/4", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.85, "locked": False,
                },
            ],
        }],
    )


def test_assign_stage_sets_hand_for_piano(db_session, sample_job, workdir):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = assign.run(ctx, _piano_score_two_hands())

    events = result["parts"][0]["measures"][0]["events"]
    # Verified directly via aura_worker.piano_hands.assign_measure: a low
    # bass note (40) and a high melody note (76) land on left/right.
    assert events[0]["hand"] == "left"
    assert events[1]["hand"] == "right"
    assert events[0]["string"] is None
    assert events[0]["fret"] is None
    validate_score(result)


def test_assign_stage_piano_out_of_range_pitch_gets_null_hand(db_session, sample_job, workdir):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    score = _piano_score_two_hands()
    score["parts"][0]["measures"][0]["events"][0]["pitch"] = 10  # below MIDI 21

    result = assign.run(ctx, score)

    events = result["parts"][0]["measures"][0]["events"]
    assert events[0]["hand"] is None
    assert events[1]["hand"] == "right"
    validate_score(result)
```

- [ ] **Step 5: Run the test file to verify everything passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_assign.py -v`
Expected: PASS (5 tests: the 2 existing guitar tests, now with the `hand is None` assertion and renamed patch target, plus the 2 new piano tests — `test_assign_stage_piano_passthrough` is gone, replaced by `test_assign_stage_sets_hand_for_piano`).

- [ ] **Step 6: Run the full worker test suite to confirm nothing else broke**

Run: `source /home/user/AuraAudio/.envrc && uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests -v`
Expected: PASS — every worker test, including `test_piano_hands.py` and `test_fingering.py` (untouched, guitar's algorithm module isn't modified by this task).

- [ ] **Step 7: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/assign.py workers/transcription/tests/test_assign.py
git commit -m "feat(worker): assign stage fills in piano hand assignment (was passthrough)"
```

---

## Task 5: MusicXML grand-staff rendering

**Files:**
- Modify: `packages/musicxml/src/musicxml/export.py`
- Modify: `packages/musicxml/tests/test_export.py`

**Interfaces:**
- Consumes: `event["hand"]` (Task 1/4, optional `"left"`/`"right"`/`null`).
- Produces: `score_json_to_musicxml` (unchanged signature) now branches on `part_data["instrument"]`: guitar keeps the existing single-staff path unchanged; piano renders a real two-staff grand staff.

- [ ] **Step 1: Write the failing tests for grand-staff rendering**

Append to `packages/musicxml/tests/test_export.py`:

```python
def _piano_score(events_by_measure: list[list[dict]]):
    return build_score(
        instrument="piano",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[
            {"number": i + 1, "events": events}
            for i, events in enumerate(events_by_measure)
        ],
    )


def _piano_event(id_: str, pitch: int, hand, onset: str) -> dict:
    return {
        "id": id_, "pitch": pitch, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": onset, "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False, "hand": hand,
    }


def test_score_json_to_musicxml_renders_piano_grand_staff(tmp_path: Path):
    score = _piano_score([[
        _piano_event("note_00", 40, "left", "0/1"),
        _piano_event("note_01", 76, "right", "1/4"),
    ]])
    out_path = tmp_path / "piano.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()

    assert "<staves>2</staves>" in content
    # verified directly: treble (right hand) is clef number 1 (G clef),
    # bass (left hand) is clef number 2 (F clef)
    assert content.index('<clef number="1">') < content.index('<clef number="2">')
    assert "<sign>G</sign>" in content
    assert "<sign>F</sign>" in content

    reopened = music21.converter.parse(str(out_path))
    reopened_notes = list(reopened.flatten().notes)
    assert len(reopened_notes) == 2
    assert {n.pitch.midi for n in reopened_notes} == {40, 76}


def test_score_json_to_musicxml_piano_out_of_range_note_still_renders(tmp_path: Path):
    # A note with hand: null (out of STANDARD_PIANO_RANGE) must still
    # appear in the file, clamped to the nearer staff — never silently
    # dropped, per the spec's explicit rule.
    score = _piano_score([[
        _piano_event("note_00", 10, None, "0/1"),  # below range -> clamps to left/bass
        _piano_event("note_01", 76, "right", "1/4"),
    ]])
    out_path = tmp_path / "piano_clamp.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()

    reopened = music21.converter.parse(str(out_path))
    reopened_notes = list(reopened.flatten().notes)
    assert len(reopened_notes) == 2  # not dropped
    assert {n.pitch.midi for n in reopened_notes} == {10, 76}


def test_score_json_to_musicxml_guitar_export_unaffected_by_piano_branch(tmp_path: Path):
    # Regression check: guitar's single-staff path must still produce
    # exactly one <part> with no <staves> element at all.
    out_path = tmp_path / "guitar_regression.musicxml"
    score_json_to_musicxml(_sample_score(), out_path)
    content = out_path.read_text()
    assert "<staves>" not in content
    assert content.count("<part ") == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests/test_export.py -v`
Expected: FAIL — the two new piano tests fail (piano currently goes through the same single-staff path as guitar, producing no `<staves>` element and no clef branching); the guitar regression test passes already (nothing changed yet) but re-run after Step 3 to confirm it still passes with the real branching code path.

- [ ] **Step 3: Update `export.py`**

Add `clef` and `layout` to the `music21` import line, and add a module-level constant:

```python
from music21 import articulations, clef, duration, instrument, key as m21_key, layout, meter as m21_meter, note, pitch as m21_pitch, stream, tempo

# packages/musicxml cannot depend on workers/transcription (packages sit
# below workers in the dependency graph) — duplicated here rather than
# imported from aura_worker.piano_hands, same reasoning as this file's
# inlined "6 - internal_string" guitar-numbering conversion.
_STANDARD_PIANO_RANGE = (21, 108)
```

Replace the whole `score_json_to_musicxml` function:

```python
def score_json_to_musicxml(score: dict, out_path: Path) -> Path:
    part_data = score["parts"][0]
    tonic_name, mode = part_data["key"].split(" ")
    key_obj = m21_key.Key(tonic_name, mode)

    if part_data["instrument"] == "piano":
        m21_score = _build_piano_grand_staff(part_data, key_obj)
    else:
        m21_score = _build_single_staff(part_data, key_obj)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m21_score.write("musicxml", fp=str(out_path))
    return out_path


def _build_single_staff(part_data: dict, key_obj: m21_key.Key) -> stream.Score:
    m21_part = stream.Part()
    m21_part.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    m21_part.insert(0, key_obj)
    m21_part.insert(0, instrument.Guitar() if part_data["instrument"] == "guitar" else instrument.Piano())

    is_first_measure = True
    for measure_data in part_data["measures"]:
        m21_measure = stream.Measure(number=measure_data["number"])
        if is_first_measure:
            # MetronomeMark must live in the Measure, not the Part — see the
            # "Validated design notes" in the implementation plan: inserting
            # it at the Part level silently drops it from the exported XML.
            m21_measure.insert(0, tempo.MetronomeMark(number=part_data["tempoBpm"]))
            is_first_measure = False
        for event in measure_data["events"]:
            n = note.Note(_spell_pitch(event["pitch"], key_obj))
            n.duration = duration.Duration(_notated_fraction_to_quarter_length(event["notatedDuration"]))
            internal_string = event.get("string")
            fret = event.get("fret")
            if internal_string is not None and fret is not None:
                musicxml_string = 6 - internal_string
                n.articulations.append(articulations.StringIndication(musicxml_string))
                n.articulations.append(articulations.FretIndication(fret))
            m21_measure.append(n)
        m21_part.append(m21_measure)

    m21_score = stream.Score()
    m21_score.insert(0, m21_part)
    return m21_score


def _hand_for_event(event: dict) -> str:
    """Which staff an event renders on: its assigned hand, or — for an
    out-of-range note (hand is None) — clamped to the nearer staff by
    pitch. The score JSON's hand: null is never mutated; this is a
    rendering-only fallback so no note is silently dropped from the file."""
    hand = event.get("hand")
    if hand is not None:
        return hand
    return "left" if event["pitch"] < _STANDARD_PIANO_RANGE[0] else "right"


def _build_piano_grand_staff(part_data: dict, key_obj: m21_key.Key) -> stream.Score:
    right = stream.PartStaff()
    right.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    right.insert(0, key_obj)
    right.insert(0, clef.TrebleClef())
    right.insert(0, instrument.Piano())

    left = stream.PartStaff()
    left.insert(0, m21_meter.TimeSignature(part_data["meter"]))
    left.insert(0, key_obj)
    left.insert(0, clef.BassClef())
    left.insert(0, instrument.Piano())

    is_first_measure = True
    for measure_data in part_data["measures"]:
        right_measure = stream.Measure(number=measure_data["number"])
        left_measure = stream.Measure(number=measure_data["number"])
        if is_first_measure:
            # Verified directly: the tempo mark only needs to go on ONE
            # staff's first measure (the right/treble one, by convention)
            # — it renders once in the output, not duplicated per staff.
            right_measure.insert(0, tempo.MetronomeMark(number=part_data["tempoBpm"]))
            is_first_measure = False
        for event in measure_data["events"]:
            n = note.Note(_spell_pitch(event["pitch"], key_obj))
            n.duration = duration.Duration(_notated_fraction_to_quarter_length(event["notatedDuration"]))
            target = right_measure if _hand_for_event(event) == "right" else left_measure
            target.append(n)
        right.append(right_measure)
        left.append(left_measure)

    # Verified directly: PartStaff (not plain Part) + StaffGroup with
    # symbol="brace" merges into ONE <part> with <staves>2</staves>, correct
    # per-staff clefs, and music21 fills a full-measure rest automatically
    # for any measure where one hand has no notes — no manual rest needed.
    staff_group = layout.StaffGroup([right, left], name="Piano", symbol="brace")
    m21_score = stream.Score()
    m21_score.insert(0, right)
    m21_score.insert(0, left)
    m21_score.insert(0, staff_group)
    return m21_score
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests -v`
Expected: PASS (17 tests: prior 14 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add packages/musicxml/src/musicxml/export.py packages/musicxml/tests/test_export.py
git commit -m "feat(musicxml): render piano grand staff (two PartStaffs, hand-based split)"
```

---

## Task 6: e2e piano coverage in CI

**Files:**
- Modify: `apps/api/tests/test_e2e_pipeline.py`

**Interfaces:**
- Consumes: the real pipeline (`run_transcription_job`), same pattern as the existing guitar e2e test.
- Produces: a new test proving the assign→export seam for piano end-to-end, closing the same kind of gap sub-project 2's final whole-branch review found and fixed after the fact (that test only existed for guitar) — this time it's written proactively as part of the plan.

- [ ] **Step 1: Write the failing test**

Append to `apps/api/tests/test_e2e_pipeline.py` (add `from test_fixtures.generate import write_diatonic_melody_wav` to the existing import line, alongside `write_guitar_pluck_wav`):

```python
def test_full_pipeline_piano_renders_grand_staff(db_session, tmp_path, s3_client):
    client = TestClient(create_app())

    fixture_path = tmp_path / "melody.wav"
    write_diatonic_melody_wav(fixture_path, key="C major", duration_s=4.0, sample_rate=44100)

    upload_resp = client.post(
        "/v1/uploads", json={"filename": "melody.wav", "content_type": "audio/wav"}
    )
    assert upload_resp.status_code == 201
    object_key = upload_resp.json()["object_key"]

    s3_client.put_object(Bucket=settings.s3_bucket, Key=object_key, Body=fixture_path.read_bytes())

    project_resp = client.post(
        "/v1/projects",
        json={"title": "E2E Piano", "instrument": "piano", "object_key": object_key},
    )
    assert project_resp.status_code == 201
    project_id = project_resp.json()["id"]

    job_resp = client.post(f"/v1/projects/{project_id}/transcriptions")
    assert job_resp.status_code == 201
    job_id = job_resp.json()["job_id"]

    run_transcription_job(job_id)

    status_resp = client.get(f"/v1/jobs/{job_id}")
    assert status_resp.json()["status"] == "succeeded", status_resp.json()

    from aura_api.models import Export

    exports = db_session.query(Export).filter_by(job_id=job_id).all()
    musicxml_export_id = next(e.id for e in exports if e.format == "musicxml")
    export_resp = client.get(f"/v1/exports/{musicxml_export_id}")
    assert export_resp.status_code == 200
    download_url = export_resp.json()["download_url"]

    import urllib.request

    with urllib.request.urlopen(download_url) as f:
        musicxml_bytes = f.read()
    assert "<staves>2</staves>" in musicxml_bytes.decode("utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `source /home/user/AuraAudio/.envrc && uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_e2e_pipeline.py -v`
Expected: FAIL — before Tasks 1-5 land this would fail at the `<staves>2</staves>` assertion (piano was a no-op passthrough); since this task runs after Tasks 1-5 in this plan's sequence, confirm instead that it fails for the *right* reason if run in isolation before this task's own code changes — there are none in this task, so this step just confirms the test is wired up correctly. Run it once to see it pass immediately if Tasks 1-5 are already done (expected in normal sequential execution), or investigate if it fails.

- [ ] **Step 3: Run test to verify it passes**

Run: `source /home/user/AuraAudio/.envrc && uv run --package aura-api pytest /home/user/AuraAudio/apps/api/tests/test_e2e_pipeline.py -v`
Expected: PASS (2 tests: the existing guitar test, and this new piano test)

- [ ] **Step 4: Commit**

```bash
git add apps/api/tests/test_e2e_pipeline.py
git commit -m "test(api): add e2e coverage for piano grand-staff export"
```

---

## Task 7: Full workspace verification

**Files:**
- None created or modified — verification only.

- [ ] **Step 1: Confirm local infra is up**

`redis-cli ping`, `pg_isready`, and the object storage endpoint used by `S3_ENDPOINT_URL` should all respond. Restart per `docs/superpowers/SESSION-HANDOFF.md`'s "Environment gotchas" section if not — this sandbox's native services have dropped mid-session multiple times already in this project's history due to container idle-restarts; it's an environment fact, not a code problem.

- [ ] **Step 2: Run the full workspace test suite**

Run: `source /home/user/AuraAudio/.envrc && cd /home/user/AuraAudio && make test`
Expected: every package's suite passes (score-schema, musicxml, test-fixtures, aura-api, aura-worker), including both e2e tests (guitar and piano).

- [ ] **Step 3: Manual smoke test — verify the grand staff reaches the exported file**

Run a piano-instrument project through the real API + worker (`POST /v1/uploads` → upload a fixture → `POST /v1/projects` with `"instrument": "piano"` → `POST /v1/projects/{id}/transcriptions` → `run_transcription_job` → `GET /v1/exports/{id}` for `musicxml` → download and inspect). Use `write_diatonic_melody_wav` (real pitched content). Confirm the downloaded MusicXML contains `<staves>2</staves>` and at least one note with `<staff>1</staff>` and one with `<staff>2</staff>`.

## Definition of Done

- A developer can feed the pipeline a piano clip and see every note carry a `hand` value **in the exported MusicXML file** (not just the internal score JSON), rendered as a real two-staff grand staff — verified by both the deterministic tests in `test_piano_hands.py`/`test_export.py` and the manual smoke test.
- Guitar projects are completely unaffected end-to-end (schema, `assign` stage, and MusicXML export all confirmed unchanged for `instrument == "guitar"`).
- Full workspace test suite passes, including committed e2e coverage for both instruments (not just a one-time manual check).
