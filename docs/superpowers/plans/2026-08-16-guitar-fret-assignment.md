# Guitar String and Fret Assignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Assign every guitar note a playable `(string, fret)` via a constrained optimizer, and render it as real tab notation (`<technical><string>/<fret></technical>`) in the exported MusicXML.

**Architecture:** A new worker stage, `assign`, runs between `quantize` and `export`. Pure algorithmic logic (candidate generation, single-note sequence DP, chord bipartite assignment via backtracking) lives in a new `aura_worker.fingering` module, independently unit-testable without any DB/storage dependency. The stage wrapper handles caching and mutates the score's events in place. The canonical score schema bumps to v3 (optional `string`/`fret` per event). `musicxml/export.py` renders the assignment, converting between this project's internal string numbering (0=low E, low-to-high) and MusicXML's tab convention (1=high E, high-to-low).

**Tech Stack:** Pure Python (no new dependencies) for the algorithm; `music21`'s `articulations.StringIndication`/`FretIndication` (verified directly against real output before this plan was written) for MusicXML rendering.

**Spec:** `docs/superpowers/specs/2026-08-16-guitar-fret-assignment-design.md`

## Global Constraints

- Standard tuning only: open-string MIDI pitches `[40, 45, 50, 55, 59, 64]` (low E to high E, internal index 0-5). `MAX_FRET = 20`.
- Hard constraint, never violated: no two simultaneous notes (a chord — same `notatedOnset` within a measure) share a string.
- A pitch with zero valid candidates is left unassigned (`string`/`fret` stay `null`) — never fails the stage, never raises `JobFailure`.
- No new `JobErrorCode` values.
- `instrument == "piano"` passes through `assign` unchanged (all events get `string: null, fret: null`).
- MusicXML string numbering is the **opposite** convention from this project's internal numbering — verified directly: MusicXML/tab convention is 1-indexed high-to-low (1 = high E), this project's internal `string` field is 0-indexed low-to-high (0 = low E). Conversion: `musicxml_string = 6 - internal_string`.
- `music21` renders tab data via `note.Note.articulations`, not a constructor argument: `n.articulations.append(articulations.StringIndication(k))` and `n.articulations.append(articulations.FretIndication(f))`.
- `schemaVersion` bumps `2` → `3` as an accepted breaking change (no migration tooling, no production data — same reasoning as the `1` → `2` bump).
- `string`/`fret` are optional (not required) properties on each event in the JSON Schema — `quantize`'s output (no `string`/`fret` keys at all) and `assign`'s output (keys present, integer or `null`) must both validate.

## File Structure

```text
packages/score_schema/src/score_schema/
  models.py       # Modify: schemaVersion 2 -> 3 in build_score()
  validate.py      # Modify: v3 schema — string/fret optional int-or-null on events
packages/score_schema/tests/
  test_models.py    # Modify: schemaVersion assertion
  test_validate.py   # Modify: v3 fixtures, string/fret coverage

workers/transcription/src/aura_worker/
  fingering.py      # Create: pure algorithm — candidates, chord assignment, sequence DP
  stage_runner.py    # Modify: STAGE_PROGRESS gains "assign": 85
  stages/
    assign.py        # Create: stage wrapper (caching, per-measure orchestration)
  runner.py         # Modify: wire assign.run into the pipeline
workers/transcription/tests/
  test_fingering.py   # Create
  test_assign.py     # Create

packages/musicxml/src/musicxml/
  export.py        # Modify: render <technical><string>/<fret> when present
packages/musicxml/tests/
  test_export.py     # Modify: new assertions for tab rendering + numbering conversion
```

---

## Task 1: `score_schema` v3 — optional `string`/`fret` fields

**Files:**
- Modify: `packages/score_schema/src/score_schema/models.py`
- Modify: `packages/score_schema/src/score_schema/validate.py`
- Modify: `packages/score_schema/tests/test_models.py`
- Modify: `packages/score_schema/tests/test_validate.py`

**Interfaces:**
- Produces: `build_score(...)` now stamps `schemaVersion: 3` (no parameter changes — `string`/`fret` are per-event fields set later by the `assign` stage, not part of `build_score`'s signature). `validate_score` accepts `schemaVersion: 3`, and permits (without requiring) `string`/`fret` on each event as integer-or-null.

- [ ] **Step 1: Update the failing test for schemaVersion 3**

In `packages/score_schema/tests/test_models.py`, change the existing `schemaVersion` assertion:

```python
    assert score["schemaVersion"] == 3
```

(replacing the prior `== 2` assertion in `test_build_score_produces_schema_v2_shape` — rename the test to `test_build_score_produces_schema_v3_shape` for clarity.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_models.py -v`
Expected: FAIL — `assert 2 == 3`

- [ ] **Step 3: Bump the constant in `models.py`**

In `build_score`'s return dict, change `"schemaVersion": 2` to `"schemaVersion": 3`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_models.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Write the failing tests for v3 validation**

Append to `packages/score_schema/tests/test_validate.py`:

```python
def test_schema_v2_is_rejected():
    score = _valid_score()
    score["schemaVersion"] = 2
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_event_without_string_or_fret_is_accepted():
    validate_score(_valid_score())  # _valid_score()'s event has no string/fret keys at all


def test_event_with_null_string_and_fret_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = None
    score["parts"][0]["measures"][0]["events"][0]["fret"] = None
    validate_score(score)  # must not raise


def test_event_with_valid_string_and_fret_is_accepted():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = 2
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 5
    validate_score(score)  # must not raise


def test_event_with_out_of_range_string_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = 6
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 0
    with pytest.raises(ScoreValidationError):
        validate_score(score)


def test_event_with_out_of_range_fret_is_rejected():
    score = _valid_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = 0
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 21
    with pytest.raises(ScoreValidationError):
        validate_score(score)
```

Update the existing `test_schema_v1_is_rejected` test (from the v1→v2 bump) to assert `score["schemaVersion"] = 1` still raises — leave it as-is, it already covers "not the current version" for v1; the new `test_schema_v2_is_rejected` above covers the more relevant "previous version" case.

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests/test_validate.py -v`
Expected: FAIL — `test_schema_v2_is_rejected` fails because `schemaVersion` is still const `2`; `test_event_with_out_of_range_string_is_rejected`/`..._fret_is_rejected` fail because `additionalProperties: False` currently rejects `string`/`fret` as unrecognized keys entirely (not because of range validation) — either way, confirms the schema doesn't yet know about these fields.

- [ ] **Step 7: Update `_SCORE_SCHEMA` and `_EVENT_SCHEMA`**

In `packages/score_schema/src/score_schema/validate.py`:

```python
_EVENT_SCHEMA = {
    "type": "object",
    "required": [
        "id", "pitch", "onsetSeconds", "offsetSeconds",
        "notatedOnset", "notatedDuration", "voice", "confidence", "locked",
    ],
    "properties": {
        "id": {"type": "string"},
        "pitch": {"type": "integer", "minimum": 0, "maximum": 127},
        "onsetSeconds": {"type": "number", "minimum": 0},
        "offsetSeconds": {"type": "number", "minimum": 0},
        "notatedOnset": {"type": "string"},
        "notatedDuration": {"type": "string"},
        "voice": {"type": "integer", "minimum": 1},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "locked": {"type": "boolean"},
        "string": {"type": ["integer", "null"], "minimum": 0, "maximum": 5},
        "fret": {"type": ["integer", "null"], "minimum": 0, "maximum": 20},
    },
    "additionalProperties": False,
}
```

Note `string`/`fret` are added to `properties` but deliberately **not** added to `required` — this is what makes both "key absent entirely" (quantize's output) and "key present as `null` or a real integer" (assign's output) valid.

Change `_SCORE_SCHEMA["properties"]["schemaVersion"]` from `{"const": 2}` to `{"const": 3}`. Everything else in `_SCORE_SCHEMA` is unchanged.

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package score-schema pytest /home/user/AuraAudio/packages/score_schema/tests -v`
Expected: PASS (all tests — prior 10 plus 6 new = 16, adjusting for the renamed test from Step 1)

- [ ] **Step 9: Commit**

```bash
git add packages/score_schema/src/score_schema/models.py packages/score_schema/src/score_schema/validate.py packages/score_schema/tests/test_models.py packages/score_schema/tests/test_validate.py
git commit -m "feat(score-schema): bump canonical score to v3 with optional string/fret fields"
```

---

## Task 2: `aura_worker.fingering` — candidate generation and chord assignment

**Files:**
- Create: `workers/transcription/src/aura_worker/fingering.py`
- Create: `workers/transcription/tests/test_fingering.py`

**Interfaces:**
- Produces: `StringFret(string: int, fret: int)` frozen dataclass; `candidates_for_pitch(pitch: int) -> list[StringFret]`; `assign_chord(pitches: list[int]) -> list[StringFret | None]` (one result per input pitch, in order; `None` where unassignable).
- Consumes: nothing (pure logic, no project imports).

- [ ] **Step 1: Write the failing test for candidate generation**

```python
# workers/transcription/tests/test_fingering.py
from aura_worker.fingering import StringFret, candidates_for_pitch


def test_candidates_for_open_low_e_string():
    candidates = candidates_for_pitch(40)  # open low E
    assert StringFret(string=0, fret=0) in candidates


def test_candidates_for_middle_c():
    candidates = candidates_for_pitch(60)
    # 60 - [40,45,50,55,59,64] = [20,15,10,5,1,-4] -> string 5 (64) is invalid (negative fret)
    expected = {
        StringFret(string=0, fret=20),
        StringFret(string=1, fret=15),
        StringFret(string=2, fret=10),
        StringFret(string=3, fret=5),
        StringFret(string=4, fret=1),
    }
    assert set(candidates) == expected


def test_candidates_for_unreachable_low_pitch():
    assert candidates_for_pitch(30) == []  # below open low E, unreachable on any string


def test_candidates_for_unreachable_high_pitch():
    assert candidates_for_pitch(90) == []  # above fret 20 on every string (64+20=84 max)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_fingering.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aura_worker.fingering'`

- [ ] **Step 3: Write `candidates_for_pitch` and supporting constants**

```python
# workers/transcription/src/aura_worker/fingering.py
from __future__ import annotations

from dataclasses import dataclass

OPEN_STRING_PITCHES = [40, 45, 50, 55, 59, 64]  # low E .. high E, internal index 0-5
MAX_FRET = 20

FRET_MOVE_WEIGHT = 1.0
STRING_CHANGE_PENALTY = 2.0
PREFERRED_MAX_FRET = 12
RANGE_PENALTY_WEIGHT = 0.5


@dataclass(frozen=True)
class StringFret:
    string: int
    fret: int


def candidates_for_pitch(pitch: int) -> list[StringFret]:
    result = []
    for string, open_pitch in enumerate(OPEN_STRING_PITCHES):
        fret = pitch - open_pitch
        if 0 <= fret <= MAX_FRET:
            result.append(StringFret(string=string, fret=fret))
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_fingering.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Write the failing tests for chord assignment**

Append to `workers/transcription/tests/test_fingering.py`:

```python
from aura_worker.fingering import assign_chord


def test_assign_chord_gives_distinct_strings():
    # C major triad: C4=60, E4=64, G4=67
    result = assign_chord([60, 64, 67])
    assert all(sf is not None for sf in result)
    strings = [sf.string for sf in result]
    assert len(set(strings)) == len(strings)  # hard constraint: all distinct


def test_assign_chord_minimizes_hand_stretch():
    # Same C major triad — the optimal distinct-string assignment is
    # 60->string3/fret5, 64->string4/fret5, 67->string5/fret3 (stretch=2),
    # not e.g. 60->string4/fret1, 64->string5/fret0, 67->string3/fret12 (stretch=12).
    result = assign_chord([60, 64, 67])
    frets = [sf.fret for sf in result]
    assert max(frets) - min(frets) <= 2


def test_assign_chord_partial_when_too_many_pitches_for_strings():
    # 7 distinct pitches can't all get distinct strings (only 6 exist) —
    # exactly one must come back None, the other 6 must still be distinct.
    pitches = [40, 45, 50, 55, 59, 64, 41]  # 41 is reachable on strings 0 and... check candidates
    result = assign_chord(pitches)
    assigned = [sf for sf in result if sf is not None]
    assert len(assigned) == 6
    strings = [sf.string for sf in assigned]
    assert len(set(strings)) == 6


def test_assign_chord_returns_none_for_unreachable_pitch_only():
    # One pitch is unreachable on any string; the other two should still
    # get a normal distinct-string assignment.
    result = assign_chord([20, 60, 64])  # 20 is unreachable (below open low E)
    assert result[0] is None
    assert result[1] is not None
    assert result[2] is not None
    assert result[1].string != result[2].string
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_fingering.py -v`
Expected: FAIL — `ImportError: cannot import name 'assign_chord'`

- [ ] **Step 7: Write `assign_chord`**

Append to `workers/transcription/src/aura_worker/fingering.py`:

```python
def assign_chord(pitches: list[int]) -> list["StringFret | None"]:
    """Assign each pitch to a distinct string, maximizing how many pitches
    get assigned at all, then minimizing hand stretch (max fret - min fret)
    among the assigned ones. Exhaustive backtracking — chords are bounded by
    6 strings, so the search space is always small."""
    per_pitch_candidates = [candidates_for_pitch(p) for p in pitches]
    n = len(pitches)

    best_result: list[StringFret | None] = [None] * n
    best_count = -1
    best_stretch: float | None = None

    def backtrack(i: int, used_strings: set[int], current: list[StringFret | None]) -> None:
        nonlocal best_result, best_count, best_stretch
        if i == n:
            count = sum(1 for x in current if x is not None)
            frets = [x.fret for x in current if x is not None]
            stretch = (max(frets) - min(frets)) if frets else 0
            if count > best_count or (count == best_count and (best_stretch is None or stretch < best_stretch)):
                best_count = count
                best_stretch = stretch
                best_result = list(current)
            return

        # Option 1: leave this pitch unassigned.
        current.append(None)
        backtrack(i + 1, used_strings, current)
        current.pop()

        # Option 2: try each candidate string for this pitch.
        for cand in per_pitch_candidates[i]:
            if cand.string in used_strings:
                continue
            current.append(cand)
            used_strings.add(cand.string)
            backtrack(i + 1, used_strings, current)
            used_strings.discard(cand.string)
            current.pop()

    backtrack(0, set(), [])
    return best_result
```

- [ ] **Step 8: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_fingering.py -v`
Expected: PASS (8 tests)

- [ ] **Step 9: Commit**

```bash
git add workers/transcription/src/aura_worker/fingering.py workers/transcription/tests/test_fingering.py
git commit -m "feat(worker): add fingering module — candidate generation and chord assignment"
```

---

## Task 3: `aura_worker.fingering` — sequence DP over a measure

**Files:**
- Modify: `workers/transcription/src/aura_worker/fingering.py`
- Modify: `workers/transcription/tests/test_fingering.py`

**Interfaces:**
- Produces: `assign_measure(events: list[dict]) -> dict[int, StringFret]` — takes a measure's `events` list (each a canonical-score event dict with at least `"pitch"` and `"notatedOnset"`), groups simultaneous notes by shared `notatedOnset`, and returns a mapping from event index (position in the input list) to its assigned `StringFret`. Indices not present in the returned dict are unassigned.
- Consumes: `StringFret`, `candidates_for_pitch`, `assign_chord` (Task 2, same module).

- [ ] **Step 1: Write the failing test for a monophonic run preferring one string**

Append to `workers/transcription/tests/test_fingering.py`:

```python
from aura_worker.fingering import assign_measure


def _event(pitch: int, onset: str) -> dict:
    return {"pitch": pitch, "notatedOnset": onset}


def test_assign_measure_chromatic_run_prefers_staying_on_one_string():
    # A short chromatic run: fret movement is cheap on one string, but
    # switching strings costs STRING_CHANGE_PENALTY (2.0) — for adjacent
    # semitones, one string should almost always win.
    events = [
        _event(50, "0/1"), _event(51, "1/4"), _event(52, "1/2"), _event(53, "3/4"),
    ]
    assignment = assign_measure(events)
    assert len(assignment) == 4
    strings = [assignment[i].string for i in range(4)]
    assert len(set(strings)) == 1  # all four notes land on the same string


def test_assign_measure_prefers_low_frets_for_open_position_chord():
    # An open-position-friendly note sequence should resolve to low frets,
    # not push into the preferred-range penalty zone (fret > 12) when a
    # low-fret option exists.
    events = [_event(40, "0/1")]  # open low E has a fret-0 candidate
    assignment = assign_measure(events)
    assert assignment[0] == StringFret(string=0, fret=0)


def test_assign_measure_handles_chords_as_one_state():
    # Two chords in sequence (each sharing an onset) — both must resolve to
    # distinct-string assignments internally, and the function must not
    # crash treating a chord as a single note.
    events = [
        _event(60, "0/1"), _event(64, "0/1"), _event(67, "0/1"),  # chord 1, onset "0/1"
        _event(62, "1/1"), _event(65, "1/1"),                      # chord 2, onset "1/1"
    ]
    assignment = assign_measure(events)
    assert len(assignment) == 5
    chord1_strings = {assignment[i].string for i in range(3)}
    assert len(chord1_strings) == 3
    chord2_strings = {assignment[i].string for i in range(3, 5)}
    assert len(chord2_strings) == 2


def test_assign_measure_skips_wholly_unreachable_chord():
    # A "chord" where every pitch is unreachable contributes no state and
    # must not break the chain between the notes before and after it.
    events = [
        _event(40, "0/1"),   # reachable
        _event(20, "1/4"),   # unreachable on any string
        _event(41, "1/2"),   # reachable
    ]
    assignment = assign_measure(events)
    assert 0 in assignment
    assert 1 not in assignment
    assert 2 in assignment
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_fingering.py -v`
Expected: FAIL — `ImportError: cannot import name 'assign_measure'`

- [ ] **Step 3: Write `assign_measure` and its helpers**

Append to `workers/transcription/src/aura_worker/fingering.py`:

```python
@dataclass
class _PlacementOption:
    representative: StringFret
    assignments: dict[int, StringFret]


def _transition_cost(prev: StringFret, curr: StringFret) -> float:
    cost = FRET_MOVE_WEIGHT * abs(curr.fret - prev.fret)
    if curr.string != prev.string:
        cost += STRING_CHANGE_PENALTY
    cost += RANGE_PENALTY_WEIGHT * max(0, curr.fret - PREFERRED_MAX_FRET)
    return cost


def _entry_cost(sf: StringFret) -> float:
    return RANGE_PENALTY_WEIGHT * max(0, sf.fret - PREFERRED_MAX_FRET)


def _measure_groups(events: list[dict]) -> list[list[int]]:
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
    if len(indices) == 1:
        idx = indices[0]
        pitch = events[idx]["pitch"]
        return [
            _PlacementOption(representative=c, assignments={idx: c})
            for c in candidates_for_pitch(pitch)
        ]

    pitches = [events[i]["pitch"] for i in indices]
    chord_result = assign_chord(pitches)
    assignments = {
        indices[j]: sf for j, sf in enumerate(chord_result) if sf is not None
    }
    if not assignments:
        return []
    representative = min(assignments.values(), key=lambda sf: sf.fret)
    return [_PlacementOption(representative=representative, assignments=assignments)]


def assign_measure(events: list[dict]) -> dict[int, StringFret]:
    groups = _measure_groups(events)
    all_steps = [_options_for_group(events, idxs) for idxs in groups]
    steps = [s for s in all_steps if s]  # drop wholly-unreachable groups

    result: dict[int, StringFret] = {}
    if not steps:
        return result

    # dp[i] = list of (cumulative_cost, backpointer_index_into_dp[i-1]) per option in steps[i]
    dp: list[list[tuple[float, int]]] = []
    for i, options in enumerate(steps):
        row: list[tuple[float, int]] = []
        if i == 0:
            for opt in options:
                row.append((_entry_cost(opt.representative), -1))
        else:
            prev_options = steps[i - 1]
            prev_row = dp[i - 1]
            for opt in options:
                best_cost = None
                best_j = -1
                for j, prev_opt in enumerate(prev_options):
                    cost = prev_row[j][0] + _transition_cost(prev_opt.representative, opt.representative)
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
        result.update(options[chosen[i]].assignments)

    return result
```

`dataclass` is already imported at the top of the file from Task 2 (`from dataclasses import dataclass`) — do not add a second import.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_fingering.py -v`
Expected: PASS (12 tests)

- [ ] **Step 5: Commit**

```bash
git add workers/transcription/src/aura_worker/fingering.py workers/transcription/tests/test_fingering.py
git commit -m "feat(worker): add sequence DP for per-measure string/fret assignment"
```

---

## Task 4: `assign` worker stage

**Files:**
- Create: `workers/transcription/src/aura_worker/stages/assign.py`
- Modify: `workers/transcription/src/aura_worker/stage_runner.py`
- Create: `workers/transcription/tests/test_assign.py`

**Interfaces:**
- Consumes: `assign_measure` (Task 3), `find_cached_artifact`/`save_artifact` (Phase 1 pattern), v3 `validate_score` (Task 1).
- Produces: `stages.assign.run(ctx: StageContext, score: dict) -> dict` — mutates and returns the score with `string`/`fret` set on every event (guitar) or `null` (piano).

- [ ] **Step 1: Write the failing test for guitar assignment**

```python
# workers/transcription/tests/test_assign.py
from score_schema.models import build_score
from score_schema.validate import validate_score

from aura_worker.stage_runner import StageContext
from aura_worker.stages import assign


class FakeStorage:
    def __init__(self):
        self.objects = {}

    def put_bytes(self, key, data):
        self.objects[key] = data

    def get_bytes(self, key):
        return self.objects[key]


def _guitar_score():
    return build_score(
        instrument="guitar",
        tempo_bpm=120.0,
        meter="4/4",
        key="C major",
        confidence={"tempo": 0.9, "meter": 0.8, "key": 0.7},
        time_map=[{"beat": 0, "seconds": 0.0}],
        measures=[{
            "number": 1,
            "events": [
                {
                    "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
            ],
        }],
    )


def test_assign_stage_sets_string_and_fret_for_guitar(db_session, sample_job, workdir):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = assign.run(ctx, _guitar_score())

    event = result["parts"][0]["measures"][0]["events"][0]
    assert event["string"] is not None
    assert event["fret"] is not None
    validate_score(result)  # must not raise — v3-shaped output


def test_assign_stage_second_call_resumes_without_recompute(db_session, sample_job, workdir, monkeypatch):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    first = assign.run(ctx, _guitar_score())

    def fail_if_called(*args, **kwargs):
        raise AssertionError("assign_measure should not be re-invoked on a cached assign stage")

    # assign.py does `from aura_worker.fingering import assign_measure`, which
    # binds the name into assign.py's own module namespace at import time —
    # patching aura_worker.fingering.assign_measure afterward would NOT affect
    # that already-bound reference. Patch it where assign.py actually looks it
    # up: aura_worker.stages.assign.assign_measure.
    monkeypatch.setattr(assign, "assign_measure", fail_if_called)

    second = assign.run(ctx, _guitar_score())
    assert second == first
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_assign.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'aura_worker.stages.assign'`

- [ ] **Step 3: Write `assign.py`**

```python
# workers/transcription/src/aura_worker/stages/assign.py
from __future__ import annotations

import hashlib
import json

from aura_worker.fingering import assign_measure
from aura_worker.stage_runner import StageContext, find_cached_artifact, save_artifact
from score_schema.validate import validate_score

STAGE_VERSION = 1


def run(ctx: StageContext, score: dict) -> dict:
    cached = find_cached_artifact(ctx, "assign", STAGE_VERSION)
    if cached is not None:
        return json.loads(ctx.storage.get_bytes(cached.object_key))

    part = score["parts"][0]
    for measure in part["measures"]:
        events = measure["events"]
        if part["instrument"] == "guitar":
            assignments = assign_measure(events)
        else:
            assignments = {}
        for i, event in enumerate(events):
            sf = assignments.get(i)
            event["string"] = sf.string if sf is not None else None
            event["fret"] = sf.fret if sf is not None else None

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

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_assign.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Write the failing test for piano passthrough**

Append to `workers/transcription/tests/test_assign.py`:

```python
def _piano_score():
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
                    "id": "note_00", "pitch": 60, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
                    "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
                    "confidence": 0.9, "locked": False,
                },
            ],
        }],
    )


def test_assign_stage_piano_passthrough(db_session, sample_job, workdir):
    storage = FakeStorage()
    ctx = StageContext(job=sample_job, session=db_session, storage=storage, workdir=workdir)

    result = assign.run(ctx, _piano_score())

    event = result["parts"][0]["measures"][0]["events"][0]
    assert event["string"] is None
    assert event["fret"] is None
    validate_score(result)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests/test_assign.py -v`
Expected: PASS (3 tests). `sample_job`'s fixture project defaults to `instrument="guitar"` per the Phase 1 `conftest.py`, but this test is unaffected by that: `assign.run` reads `score["parts"][0]["instrument"]` (the score's own recorded instrument, set by `quantize` from `ctx.job.project.instrument` back when the score was built) — never `ctx.job.project.instrument` directly — so passing a piano-instrument score produces piano passthrough regardless of what instrument `sample_job`'s underlying project happens to be.

- [ ] **Step 7: Wire `assign` into `STAGE_PROGRESS`**

```python
# workers/transcription/src/aura_worker/stage_runner.py
STAGE_PROGRESS = {
    "probe": 10,
    "normalize": 25,
    "inference": 55,
    "structure": 65,
    "quantize": 75,
    "assign": 85,
    "export": 100,
}
```

- [ ] **Step 8: Commit**

```bash
git add workers/transcription/src/aura_worker/stages/assign.py workers/transcription/src/aura_worker/stage_runner.py workers/transcription/tests/test_assign.py
git commit -m "feat(worker): add assign stage — guitar string/fret assignment, piano passthrough"
```

---

## Task 5: MusicXML tab rendering

**Files:**
- Modify: `packages/musicxml/src/musicxml/export.py`
- Modify: `packages/musicxml/tests/test_export.py`

**Interfaces:**
- Consumes: `event["string"]`/`event["fret"]` (Task 1/4, optional int-or-null).
- Produces: `score_json_to_musicxml` (unchanged signature) now appends `<technical><string>/<fret></technical>` when both are non-null, converting internal (0=low E) to MusicXML (1=high E) numbering.

- [ ] **Step 1: Write the failing test for tab rendering**

Append to `packages/musicxml/tests/test_export.py`:

```python
def test_score_json_to_musicxml_renders_string_and_fret(tmp_path: Path):
    score = _sample_score()
    # internal string=2 (low-to-high, 0-indexed) -> MusicXML string 6-2=4
    score["parts"][0]["measures"][0]["events"][0]["string"] = 2
    score["parts"][0]["measures"][0]["events"][0]["fret"] = 5
    out_path = tmp_path / "tab.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<string>4</string>" in content
    assert "<fret>5</fret>" in content


def test_score_json_to_musicxml_omits_technical_block_when_unassigned(tmp_path: Path):
    score = _sample_score()
    score["parts"][0]["measures"][0]["events"][0]["string"] = None
    score["parts"][0]["measures"][0]["events"][0]["fret"] = None
    out_path = tmp_path / "no_tab.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<technical>" not in content


def test_score_json_to_musicxml_omits_technical_block_when_keys_absent(tmp_path: Path):
    # A score built without ever running the assign stage (e.g. a piano
    # score, or a guitar score from before assign ran) has no string/fret
    # keys on its events at all — export must not crash on the missing keys.
    score = _sample_score()
    out_path = tmp_path / "no_keys.musicxml"
    score_json_to_musicxml(score, out_path)
    content = out_path.read_text()
    assert "<technical>" not in content
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests/test_export.py -v`
Expected: FAIL — `test_score_json_to_musicxml_renders_string_and_fret` fails (`<technical>` never emitted); the other two pass already by coincidence (nothing to omit yet) but re-run after Step 3 to confirm they still pass with the real code path.

- [ ] **Step 3: Update `export.py`**

Add `articulations` to the `music21` import line:

```python
from music21 import articulations, duration, instrument, key as m21_key, meter as m21_meter, note, pitch as m21_pitch, stream, tempo
```

In the event loop inside `score_json_to_musicxml`, after building `n` and setting its duration, before `m21_measure.append(n)`:

```python
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
```

(replacing the existing three-line loop body with this five-line version — `event.get(...)` rather than `event[...]` handles both "key present as `null`" and "key absent entirely" uniformly, since both return `None` from `.get()`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run --package musicxml pytest /home/user/AuraAudio/packages/musicxml/tests -v`
Expected: PASS (14 tests: prior 11 + 3 new)

- [ ] **Step 5: Commit**

```bash
git add packages/musicxml/src/musicxml/export.py packages/musicxml/tests/test_export.py
git commit -m "feat(musicxml): render string/fret as MusicXML technical notation"
```

---

## Task 6: Runner wiring

**Files:**
- Modify: `workers/transcription/src/aura_worker/runner.py`

**Interfaces:**
- Consumes: `assign.run(ctx, score) -> dict` (Task 4).
- Produces: pipeline order becomes probe → normalize → inference → structure → quantize → **assign** → export.

- [ ] **Step 1: Update the import line**

```python
from aura_worker.stages import export as export_stage
from aura_worker.stages import assign, inference, normalize, probe, quantize, structure
```

(adding `assign` to the existing `from aura_worker.stages import inference, normalize, probe, quantize, structure` line.)

- [ ] **Step 2: Insert the `assign.run` call**

```python
            structure_result = structure.run(ctx, normalized_path=normalized_path, notes=notes)
            score = quantize.run(ctx, notes, structure_result)
            score = assign.run(ctx, score)
            export_stage.run(ctx, notes=notes, score=score)
```

(inserting the `score = assign.run(ctx, score)` line between the existing `quantize.run` and `export_stage.run` calls.)

- [ ] **Step 3: Run the full worker test suite**

Run: `source /home/user/AuraAudio/.envrc && uv run --package aura-worker pytest /home/user/AuraAudio/workers/transcription/tests -v` (from `/home/user/AuraAudio`)
Expected: PASS — every worker test, including the pre-existing `test_export.py` stage tests (they build their own scores independently of the pipeline and don't touch `runner.py`, so they're unaffected) and any pipeline-level test.

- [ ] **Step 4: Commit**

```bash
git add workers/transcription/src/aura_worker/runner.py
git commit -m "feat(worker): wire assign stage into the pipeline between quantize and export"
```

---

## Task 7: Full workspace verification

**Files:**
- None created or modified — verification only.

- [ ] **Step 1: Confirm local infra is up**

`redis-cli ping`, `pg_isready`, and the object storage endpoint used by `S3_ENDPOINT_URL` should all respond. Restart per `docs/superpowers/SESSION-HANDOFF.md`'s "Environment gotchas" section if not.

- [ ] **Step 2: Run the full workspace test suite**

Run: `source /home/user/AuraAudio/.envrc && cd /home/user/AuraAudio && make test`
Expected: every package's suite passes, including the untouched `apps/api/tests/test_e2e_pipeline.py` — the pipeline now runs one more stage (`assign`) before export, but that test's fixture is a piano-instrument project by default (check `test_e2e_pipeline.py`'s `POST /v1/projects` call — if it's guitar, `assign` runs the full algorithm; if piano, it's a no-op passthrough; either way it should not fail).

- [ ] **Step 3: Manual smoke test — verify tab notation reaches the exported file**

Run a guitar-instrument project through the real API + worker (same pattern as the beat/meter/key plan's Task 7 Step 3 manual check — `POST /v1/uploads` → upload a fixture → `POST /v1/projects` with `"instrument": "guitar"` → `POST /v1/projects/{id}/transcriptions` → `run_transcription_job` → `GET /v1/exports/{id}` for `musicxml` → download and inspect). Use `write_diatonic_melody_wav` (has real pitched content, unlike the metronome click fixture — same reasoning as the beat/meter/key plan's corrected Task 7). Confirm the downloaded MusicXML contains at least one `<technical><string>` element with a value in `1`-`6`.

## Definition of Done

- A developer can feed the pipeline a guitar clip and see every reachable note carry a valid `(string, fret)` in the exported MusicXML, with the hard constraint (distinct strings within a chord) never violated — verified by both the property-style tests in `test_fingering.py` and the manual smoke test.
- Piano projects are unaffected end-to-end.
- Full workspace test suite passes.
