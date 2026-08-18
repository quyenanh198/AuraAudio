# Semantic Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user fix a transcription in place — select a note on the OSMD score and change pitch/timing/fingering/hand, delete/add notes, correct key/tempo/meter, with undo/redo/revert — backend-authoritative, with notation, exports, and both playback sources always reflecting the edited state.

**Architecture:** Pure edit operations live in `score_schema.edits` and are applied by a new `edits` API router that walks history through the EXISTING `ScoreRevision` table (head pointer in `Project.settings["scoreHeadRevisionId"]` — no migration). The slow half (lock-aware fingering/hand DP re-run + MusicXML/MIDI re-export) runs as a coalesced `rederive` job on the existing in-process queue, observable via `GET /v1/jobs/{id}`. The frontend adds click-selection on the OSMD canvas (reusing the playback timeline's cursor-walk correlation), an Inspector in the sidebar, keyboard shortcuts, and an "updating…" refresh loop.

**Tech Stack:** Python (FastAPI/SQLAlchemy/jsonschema, music21 via existing `musicxml` package, mido via existing export stage), Svelte 5 + TS, OSMD 2.1.2, Vitest, pytest.

**Spec:** `docs/superpowers/specs/2026-08-18-semantic-editing-design.md`

## Global Constraints

- Fixed port `8317`; base URL literal only in `apps/desktop/web/src/lib/api.ts`.
- Fully offline at runtime; no CDN/webfonts.
- `aura_api.main` untouched by CORS logic; no wildcard on `/v1/*`; existing routes keep their shapes — new endpoints only, NO schema migrations (head pointer lives in the existing `Project.settings` JSON column).
- Immutability rule: `apply_edit` returns a NEW score dict; never mutates its input.
- Third-party API names (OSMD internals, music21, mido) verified against installed packages/typings, never memory. Python code in this plan is written against the real codebase and IS exact unless a step says "verify".
- Visual language: dark UI `#1e1d21`/`#26242a`, border `#37343c`, text `#e8e5df`/dim `#9b968c`, amber `#d99a4e`, paper `#f5f1e8`, system-ui. Frontend follows existing component patterns (Svelte 5 runes, explicit `$state<T | null>(null)` generics).
- Tests: Vitest frontend; backend follows existing patterns (`db_session` fixture, unconditional env overrides — never `setdefault`; real `LocalStorageClient` via the `test_uploads.py`/`test_scores_endpoints.py` monkeypatch idiom).
- Environment: GUI verification via `pkill -f vite; cd /home/user/AuraAudio && xvfb-run -a cargo tauri dev --config apps/desktop/src-tauri/tauri.conf.json`; rebuild the bundled backend (`bash apps/desktop/build-backend.sh`) after ANY backend/package change before app-level verification.

## File Structure

```text
packages/score_schema/src/score_schema/edits.py     # NEW: pure ops + time math (T1)
packages/score_schema/tests/test_edits.py           # NEW (T1)
workers/transcription/src/aura_worker/fingering.py  # lock param (T2)
workers/transcription/src/aura_worker/piano_hands.py# lock param (T2)
workers/transcription/tests/test_fingering_locks.py # NEW (T2)
workers/transcription/src/aura_worker/rederive.py   # NEW: run_rederive_job (T3)
workers/transcription/tests/test_rederive.py        # NEW (T3)
apps/api/src/aura_api/queue.py                      # + enqueue_rederive_job (T3)
apps/api/src/aura_api/routers/edits.py              # NEW: edit endpoints (T4)
apps/api/src/aura_api/routers/scores.py             # head-aware score resolution (T4)
apps/api/src/aura_api/schemas.py                    # + EditResponse models (T4)
apps/api/src/aura_api/main.py                       # include edits router (T4)
apps/api/tests/test_edits_endpoints.py              # NEW (T4)
apps/desktop/web/src/lib/api.ts                     # + edit calls (T5)
apps/desktop/web/src/lib/editor.ts                  # NEW: editor store (T5)
apps/desktop/web/src/lib/editor.test.ts             # NEW (T5)
apps/desktop/web/src/components/Notation.svelte     # hit-testing + selection (T6)
apps/desktop/web/src/lib/correlate.ts               # NEW: click→eventId (T6, unit-tested)
apps/desktop/web/src/components/Sidebar.svelte      # Inspector + editable facts (T7)
apps/desktop/web/src/components/ScoreView.svelte    # wiring, keyboard, refresh (T6/T7)
docs/superpowers/SESSION-HANDOFF.md                 # T8
```

---

### Task 1: `score_schema.edits` — pure operations + time math

**Files:**
- Create: `packages/score_schema/src/score_schema/edits.py`
- Create: `packages/score_schema/tests/test_edits.py`

**Interfaces:**
- Produces: `EditError(ValueError)` with `.reason: str`; `apply_edit(score: dict, op: dict) -> dict` (new dict, input untouched); op shapes exactly as the spec §3 table (`{"type": "set_pitch", "eventId": ..., "pitch": ...}` etc.). Helper exports used by tests and T3: `beats_per_measure(meter: str) -> Fraction`, `seconds_per_beat(time_map: list[dict]) -> float`.

- [ ] **Step 1: Read the ground truth** — `packages/score_schema/src/score_schema/validate.py` (event schema: required fields incl. `locked`; `string` 0-5, `fret` 0-20, `hand` in left/right/None), `workers/transcription/src/aura_worker/stages/quantize.py` (fraction-of-whole-note encoding for `notatedOnset`/`notatedDuration`; `GRID_BEATS = Fraction(1, 4)`; measure numbering from 1; `time_map = [{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": spb}]`), and `workers/transcription/src/aura_worker/stages/structure.py` (`METER_CANDIDATES` keys — copy the literal meter strings into `_ALLOWED_METERS` below so score_schema stays standalone).

- [ ] **Step 2: Write the failing tests** (representative set — cover EVERY op, happy + invalid):

```python
# packages/score_schema/tests/test_edits.py
import pytest

from score_schema.edits import EditError, apply_edit


def _score(events=None, meter="4/4", tempo=120.0):
    spb = 60.0 / tempo
    default_events = [{
        "id": "note_00", "pitch": 52, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
        "notatedOnset": "0/1", "notatedDuration": "1/4", "voice": 1,
        "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None,
    }]
    return {
        "schemaVersion": 4,
        "timeMap": [{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": spb}],
        "parts": [{
            "instrument": "guitar", "tempoBpm": tempo, "meter": meter, "key": "E minor",
            "confidence": {"tempo": 0.9, "meter": 0.8, "key": 0.7},
            "measures": [{"number": 1, "events": events or default_events}],
        }],
    }


def test_set_pitch_changes_pitch_and_locks_without_mutating_input():
    score = _score()
    out = apply_edit(score, {"type": "set_pitch", "eventId": "note_00", "pitch": 60})
    ev = out["parts"][0]["measures"][0]["events"][0]
    assert ev["pitch"] == 60 and ev["locked"] is True
    assert score["parts"][0]["measures"][0]["events"][0]["pitch"] == 52  # input untouched


def test_move_note_recomputes_seconds_from_time_map():
    score = _score()  # 120 bpm -> 0.5 s/beat
    out = apply_edit(score, {"type": "move_note", "eventId": "note_00", "notatedOnset": "1/4"})
    ev = out["parts"][0]["measures"][0]["events"][0]
    assert ev["notatedOnset"] == "1/4"          # beat 1 of measure 1
    assert ev["onsetSeconds"] == pytest.approx(0.5)
    assert ev["offsetSeconds"] == pytest.approx(1.0)  # duration 1/4 whole = 1 beat


def test_move_note_beyond_measure_rejected():
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "move_note", "eventId": "note_00", "notatedOnset": "9/8"})


def test_delete_then_unknown_event_rejected():
    out = apply_edit(_score(), {"type": "delete_note", "eventId": "note_00"})
    assert out["parts"][0]["measures"][0]["events"] == []
    with pytest.raises(EditError):
        apply_edit(out, {"type": "set_pitch", "eventId": "note_00", "pitch": 60})


def test_add_note_generates_id_and_seconds_and_requires_existing_measure():
    out = apply_edit(_score(), {"type": "add_note", "measureNumber": 1,
                                "notatedOnset": "1/2", "notatedDuration": "1/4",
                                "pitch": 64, "voice": 1})
    events = out["parts"][0]["measures"][0]["events"]
    added = [e for e in events if e["pitch"] == 64][0]
    assert added["locked"] is True and added["onsetSeconds"] == pytest.approx(1.0)
    assert added["id"] not in {"note_00"}
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "add_note", "measureNumber": 7,
                              "notatedOnset": "0/1", "notatedDuration": "1/4",
                              "pitch": 64, "voice": 1})


def test_set_fingering_and_hand_validate_instrument():
    out = apply_edit(_score(), {"type": "set_fingering", "eventId": "note_00",
                                "string": 4, "fret": 7})
    ev = out["parts"][0]["measures"][0]["events"][0]
    assert (ev["string"], ev["fret"], ev["locked"]) == (4, 7, True)
    with pytest.raises(EditError):  # hand op on a guitar part
        apply_edit(_score(), {"type": "set_hand", "eventId": "note_00", "hand": "left"})


def test_set_part_fact_tempo_rescales_all_seconds():
    out = apply_edit(_score(), {"type": "set_part_fact", "field": "tempoBpm", "value": 60.0})
    part = out["parts"][0]
    assert part["tempoBpm"] == 60.0
    assert out["timeMap"][1]["seconds"] == pytest.approx(1.0)
    ev = part["measures"][0]["events"][0]
    assert ev["onsetSeconds"] == pytest.approx(0.0) and ev["offsetSeconds"] == pytest.approx(1.0)


def test_set_part_fact_meter_rebuckets_measures():
    events = [
        {"id": f"note_{i:02d}", "pitch": 52, "onsetSeconds": i * 0.5, "offsetSeconds": i * 0.5 + 0.5,
         "notatedOnset": f"{i}/4", "notatedDuration": "1/4", "voice": 1,
         "confidence": 0.9, "locked": False, "string": 5, "fret": 2, "hand": None}
        for i in range(4)  # beats 0..3 of one 4/4 measure
    ]
    out = apply_edit(_score(events=events), {"type": "set_part_fact", "field": "meter", "value": "3/4"})
    measures = out["parts"][0]["measures"]
    assert [m["number"] for m in measures] == [1, 2]
    assert len(measures[0]["events"]) == 3 and len(measures[1]["events"]) == 1
    assert measures[1]["events"][0]["notatedOnset"] == "0/1"  # beat 3 -> measure 2 beat 0


def test_invalid_op_type_and_pitch_bounds_rejected():
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "explode"})
    with pytest.raises(EditError):
        apply_edit(_score(), {"type": "set_pitch", "eventId": "note_00", "pitch": 128})
```

- [ ] **Step 3: Run to verify failure** — `uv run --package score-schema pytest packages/score_schema/tests/test_edits.py -v` (check the real package name in `packages/score_schema/pyproject.toml` and use it). Expected: `ModuleNotFoundError: score_schema.edits`.

- [ ] **Step 4: Implement `edits.py`**

```python
# packages/score_schema/src/score_schema/edits.py
from __future__ import annotations

import copy
import uuid
from fractions import Fraction

from score_schema.validate import validate_score

# Mirrors aura_worker.stages.structure.METER_CANDIDATES keys (copied so this
# package stays standalone); verify against that file and keep in sync.
_ALLOWED_METERS = ("4/4", "3/4", "6/8")


class EditError(ValueError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def beats_per_measure(meter: str) -> Fraction:
    num, den = meter.split("/")
    return Fraction(int(num)) * Fraction(4, int(den))


def seconds_per_beat(time_map: list[dict]) -> float:
    b0, b1 = time_map[0], time_map[1]
    return (b1["seconds"] - b0["seconds"]) / (b1["beat"] - b0["beat"])


def _fraction(text: str, what: str) -> Fraction:
    try:
        num, den = text.split("/")
        f = Fraction(int(num), int(den))
    except (ValueError, ZeroDivisionError) as exc:
        raise EditError(f"invalid {what}: {text!r}") from exc
    if f < 0:
        raise EditError(f"{what} must be >= 0")
    return f


def _find_event(part: dict, event_id: str) -> tuple[dict, dict]:
    for measure in part["measures"]:
        for event in measure["events"]:
            if event["id"] == event_id:
                return measure, event
    raise EditError(f"unknown event: {event_id}")


def _retime(score: dict, part: dict, measure: dict, event: dict) -> None:
    """Recompute onsetSeconds/offsetSeconds from notated position + timeMap."""
    spb = seconds_per_beat(score["timeMap"])
    bpm_frac = beats_per_measure(part["meter"])
    onset_beats = _fraction(event["notatedOnset"], "notatedOnset") * 4
    duration_beats = _fraction(event["notatedDuration"], "notatedDuration") * 4
    absolute = (measure["number"] - 1) * bpm_frac + onset_beats
    event["onsetSeconds"] = float(absolute) * spb
    event["offsetSeconds"] = float(absolute + duration_beats) * spb


def _rebucket(part: dict, old_meter: str, new_meter: str) -> None:
    """Reassign events to measures for a new meter, preserving absolute beats."""
    old_bpm = beats_per_measure(old_meter)
    new_bpm = beats_per_measure(new_meter)
    flat: list[tuple[Fraction, dict]] = []
    for measure in part["measures"]:
        for event in measure["events"]:
            onset_beats = _fraction(event["notatedOnset"], "notatedOnset") * 4
            flat.append(((measure["number"] - 1) * old_bpm + onset_beats, event))
    flat.sort(key=lambda pair: pair[0])
    buckets: dict[int, list[dict]] = {}
    for absolute, event in flat:
        number = int(absolute // new_bpm) + 1
        within = absolute - (number - 1) * new_bpm
        whole = within / 4
        event["notatedOnset"] = f"{whole.numerator}/{whole.denominator}"
        buckets.setdefault(number, []).append(event)
    part["measures"] = [
        {"number": number, "events": events}
        for number, events in sorted(buckets.items())
    ]


def apply_edit(score: dict, op: dict) -> dict:
    out = copy.deepcopy(score)
    part = out["parts"][0]
    kind = op.get("type")

    if kind == "set_pitch":
        if not isinstance(op.get("pitch"), int) or not 0 <= op["pitch"] <= 127:
            raise EditError("pitch must be an integer 0-127")
        _, event = _find_event(part, op["eventId"])
        event["pitch"] = op["pitch"]
        event["locked"] = True

    elif kind == "move_note":
        measure, event = _find_event(part, op["eventId"])
        onset_beats = _fraction(op["notatedOnset"], "notatedOnset") * 4
        if onset_beats >= beats_per_measure(part["meter"]):
            raise EditError("notatedOnset outside the measure")
        event["notatedOnset"] = op["notatedOnset"]
        event["locked"] = True
        _retime(out, part, measure, event)

    elif kind == "set_duration":
        duration_beats = _fraction(op["notatedDuration"], "notatedDuration") * 4
        if duration_beats <= 0:
            raise EditError("notatedDuration must be > 0")
        measure, event = _find_event(part, op["eventId"])
        event["notatedDuration"] = op["notatedDuration"]
        event["locked"] = True
        _retime(out, part, measure, event)

    elif kind == "delete_note":
        measure, event = _find_event(part, op["eventId"])
        measure["events"].remove(event)

    elif kind == "add_note":
        numbers = {m["number"]: m for m in part["measures"]}
        measure = numbers.get(op.get("measureNumber"))
        if measure is None:
            raise EditError(f"measure {op.get('measureNumber')} does not exist")
        if not isinstance(op.get("pitch"), int) or not 0 <= op["pitch"] <= 127:
            raise EditError("pitch must be an integer 0-127")
        onset_beats = _fraction(op["notatedOnset"], "notatedOnset") * 4
        if onset_beats >= beats_per_measure(part["meter"]):
            raise EditError("notatedOnset outside the measure")
        event = {
            "id": f"note_{uuid.uuid4().hex[:8]}",
            "pitch": op["pitch"], "onsetSeconds": 0.0, "offsetSeconds": 0.0,
            "notatedOnset": op["notatedOnset"], "notatedDuration": op["notatedDuration"],
            "voice": op.get("voice", 1), "confidence": 1.0, "locked": True,
            "string": None, "fret": None, "hand": None,
        }
        _retime(out, part, measure, event)
        measure["events"].append(event)
        measure["events"].sort(key=lambda e: _fraction(e["notatedOnset"], "notatedOnset"))

    elif kind == "set_fingering":
        if part["instrument"] != "guitar":
            raise EditError("set_fingering only applies to guitar parts")
        if not isinstance(op.get("string"), int) or not 0 <= op["string"] <= 5:
            raise EditError("string must be an integer 0-5")
        if not isinstance(op.get("fret"), int) or not 0 <= op["fret"] <= 20:
            raise EditError("fret must be an integer 0-20")
        _, event = _find_event(part, op["eventId"])
        event["string"], event["fret"], event["locked"] = op["string"], op["fret"], True

    elif kind == "set_hand":
        if part["instrument"] != "piano":
            raise EditError("set_hand only applies to piano parts")
        if op.get("hand") not in ("left", "right"):
            raise EditError("hand must be 'left' or 'right'")
        _, event = _find_event(part, op["eventId"])
        event["hand"], event["locked"] = op["hand"], True

    elif kind == "set_locked":
        if not isinstance(op.get("locked"), bool):
            raise EditError("locked must be a boolean")
        _, event = _find_event(part, op["eventId"])
        event["locked"] = op["locked"]

    elif kind == "set_part_fact":
        field, value = op.get("field"), op.get("value")
        if field == "tempoBpm":
            if not isinstance(value, (int, float)) or not 20 <= value <= 300:
                raise EditError("tempoBpm must be a number 20-300")
            part["tempoBpm"] = float(value)
            spb = 60.0 / float(value)
            out["timeMap"] = [{"beat": 0, "seconds": 0.0}, {"beat": 1, "seconds": spb}]
            for measure in part["measures"]:
                for event in measure["events"]:
                    _retime(out, part, measure, event)
        elif field == "meter":
            if value not in _ALLOWED_METERS:
                raise EditError(f"meter must be one of {_ALLOWED_METERS}")
            old_meter = part["meter"]
            part["meter"] = value
            _rebucket(part, old_meter, value)
            for measure in part["measures"]:
                for event in measure["events"]:
                    _retime(out, part, measure, event)
        elif field == "key":
            if not isinstance(value, str) or not value.strip():
                raise EditError("key must be a non-empty string")
            part["key"] = value
        else:
            raise EditError(f"unknown part fact: {field}")

    else:
        raise EditError(f"unknown edit type: {kind}")

    validate_score(out)
    return out
```

- [ ] **Step 5: Run to pass**, then the package's full suite. Fix only what the tests reveal (e.g. if `validate_score` requires fields the code above got wrong, the schema wins).

- [ ] **Step 6: Commit** — `git add packages/score_schema && git commit -m "feat(score_schema): pure semantic edit operations"`

---

### Task 2: Lock-aware fingering and hand assignment

**Files:**
- Modify: `workers/transcription/src/aura_worker/fingering.py` (`assign_measure`)
- Modify: `workers/transcription/src/aura_worker/piano_hands.py` (`assign_measure`)
- Create: `workers/transcription/tests/test_fingering_locks.py`

**Interfaces:**
- Consumes: existing `assign_measure(events: list[dict]) -> dict[int, StringFret]` (fingering) and `-> dict[int, str]` (piano_hands); both group same-onset events and run a sequence DP over per-group placement options.
- Produces: `assign_measure(events, locked: dict[int, ...] | None = None)` — same return types; for any index in `locked`, the returned assignment EQUALS the locked value, and the DP optimizes the rest around it. Default `None` keeps today's behavior byte-identical (existing tests must pass unchanged).

- [ ] **Step 1: Read both DP implementations end to end** (`fingering.py`, `piano_hands.py`) — understand `_measure_groups`/`_group_by_onset`, `_options_for_group`, and the DP loop before touching anything.

- [ ] **Step 2: Write the failing tests**

```python
# workers/transcription/tests/test_fingering_locks.py
from aura_worker.fingering import StringFret, assign_measure as assign_guitar
from aura_worker.piano_hands import assign_measure as assign_piano


def _ev(pitch, onset="0/1"):
    return {"id": f"e{pitch}", "pitch": pitch, "notatedOnset": onset,
            "notatedDuration": "1/4", "voice": 1, "confidence": 0.9,
            "locked": False, "onsetSeconds": 0.0, "offsetSeconds": 0.5,
            "string": None, "fret": None, "hand": None}


def test_guitar_locked_note_keeps_its_placement():
    events = [_ev(52, "0/1"), _ev(55, "1/4")]
    locked = {0: StringFret(string=3, fret=9)}  # E3 on string 3 fret 9 (verify a
    # real candidate via candidates_for_pitch(52) and use one that is NOT the
    # DP's unconstrained choice — assert the unconstrained run differs first)
    unconstrained = assign_guitar(events)
    assert unconstrained[0] != locked[0]
    result = assign_guitar(events, locked=locked)
    assert result[0] == locked[0]
    assert 1 in result  # neighbor still assigned


def test_guitar_locked_chord_member_still_respected():
    events = [_ev(52, "0/1"), _ev(57, "0/1")]  # chord
    locked = {0: StringFret(string=3, fret=9)}
    result = assign_guitar(events, locked=locked)
    assert result[0] == locked[0]
    assert result[1].string != locked[0].string  # no string collision


def test_piano_locked_hand_respected():
    events = [_ev(40, "0/1"), _ev(76, "1/4")]
    result = assign_piano(events, locked={0: "right"})  # against register intuition
    assert result[0] == "right"
    assert result[1] in ("left", "right")


def test_no_locks_matches_previous_behavior():
    events = [_ev(52, "0/1"), _ev(55, "1/4")]
    assert assign_guitar(events) == assign_guitar(events, locked=None)
```

(Adjust the locked `StringFret` literals to REAL candidates from `candidates_for_pitch` — the test asserts the unconstrained choice differs before asserting the lock wins, so the test cannot pass vacuously.)

- [ ] **Step 3: Run to verify failure** — `uv run --package aura-worker pytest workers/transcription/tests/test_fingering_locks.py -v` (verify the package name in `workers/transcription/pyproject.toml`). Expected: `TypeError: assign_measure() got an unexpected keyword argument 'locked'`.

- [ ] **Step 4: Implement.** In each module, add `locked: dict[int, StringFret] | None = None` (resp. `dict[int, str] | None`) to `assign_measure`. Inside `_options_for_group` (or at its call site — pick the smaller diff), filter each group's options to those where every locked member index gets exactly its locked value. If the filter empties a group's options (a lock combination the generator never proposes), synthesize one option that pins the locked values and assigns remaining members via the existing per-chord assigner (`assign_chord` for guitar; for piano, remaining members keep the split's side by register threshold as `candidate_splits` does — read it and reuse). The DP loop itself is untouched.

- [ ] **Step 5: Run new tests + the FULL worker suite** (`uv run --package aura-worker pytest workers/transcription/tests -v`) — the no-locks path must be byte-identical (existing DP tests unchanged and green).

- [ ] **Step 6: Commit** — `feat(worker): lock-aware fingering and hand assignment`

---

### Task 3: Re-derive job (lock-aware re-assign + re-export) + queue wiring

**Files:**
- Create: `workers/transcription/src/aura_worker/rederive.py`
- Modify: `apps/api/src/aura_api/queue.py` (add `enqueue_rederive_job`)
- Create: `workers/transcription/tests/test_rederive.py`

**Interfaces:**
- Consumes: T2's `assign_measure(..., locked=...)`; `musicxml.export.score_json_to_musicxml(score, path)`; the existing export stage's `_write_midi` shape (mido usage — read `workers/transcription/src/aura_worker/stages/export.py:10-37` and mirror it, but sourcing timing from score events).
- Produces: `run_rederive_job(job_id: str) -> None` (module `aura_worker.rederive`); `enqueue_rederive_job(job_id: str) -> None` in `aura_api.queue`. Contract with T4: the job row is a `TranscriptionJob` with `stage="rederive"`; on completion `status="succeeded"`, `progress=100`; the head `ScoreRevision.score_json` holds the re-assigned score; the project's `Export` rows (midi + musicxml) have `revision` = head revision's `revision` and fresh `object_key`s `projects/{project_id}/exports/rev{revision}/output.{mid,musicxml}`.

- [ ] **Step 1: Write the failing test**

```python
# workers/transcription/tests/test_rederive.py — key scenarios; follow the
# existing worker test files' session/storage fixture idiom exactly.
def test_rederive_reassigns_unlocked_updates_revision_and_exports(...):
    # Arrange: project (guitar) + succeeded transcription job + assign artifact;
    # bootstrap a ScoreRevision whose score has one LOCKED event with a
    # non-default string/fret and one unlocked event; head pointer in
    # project.settings["scoreHeadRevisionId"]; existing Export rows (midi,
    # musicxml) from the transcription.
    # Act: run_rederive_job(rederive_job_id)
    # Assert: locked event kept its string/fret; unlocked event got a DP
    # assignment (not None); revision.score_json updated in place;
    # Export rows' object_keys now under projects/{pid}/exports/rev{n}/ and
    # the musicxml blob parses (starts with b"<?xml"); job row succeeded.

def test_rederive_superseded_job_skips(...):
    # Two rederive job rows for one project; run the OLDER one; assert it
    # completes as succeeded WITHOUT touching exports (object_keys unchanged),
    # then run the newer one and assert it does the work.

def test_rederive_export_failure_marks_job_failed_but_keeps_revision(...):
    # Monkeypatch score_json_to_musicxml to raise; assert job failed with
    # error_detail set, revision.score_json still the edited score.
```

- [ ] **Step 2: Run to verify failure** (`ModuleNotFoundError: aura_worker.rederive`).

- [ ] **Step 3: Implement `rederive.py`**

```python
# workers/transcription/src/aura_worker/rederive.py
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.orm.attributes import flag_modified

from aura_api.db import get_engine
from aura_api.models import Export, Project, ScoreRevision, TranscriptionJob
from aura_api.storage import LocalStorageClient
from aura_worker.fingering import StringFret, assign_measure as assign_string_fret
from aura_worker.piano_hands import assign_measure as assign_hands
from musicxml.export import score_json_to_musicxml
from score_schema.validate import validate_score

logger = logging.getLogger(__name__)

_SessionLocal = sessionmaker(bind=get_engine())


def _reassign_with_locks(score: dict) -> dict:
    part = score["parts"][0]
    instrument = part["instrument"]
    for measure in part["measures"]:
        events = measure["events"]
        if instrument == "guitar":
            locked = {
                i: StringFret(string=e["string"], fret=e["fret"])
                for i, e in enumerate(events)
                if e["locked"] and e["string"] is not None and e["fret"] is not None
            }
            assignments = assign_string_fret(events, locked=locked)
            for i, event in enumerate(events):
                sf = assignments.get(i)
                event["string"] = sf.string if sf is not None else None
                event["fret"] = sf.fret if sf is not None else None
                event["hand"] = None
        else:
            locked = {
                i: e["hand"] for i, e in enumerate(events)
                if e["locked"] and e["hand"] in ("left", "right")
            }
            assignments = assign_hands(events, locked=locked)
            for i, event in enumerate(events):
                event["hand"] = assignments.get(i)
                event["string"] = None
                event["fret"] = None
    validate_score(score)
    return score


def _write_midi_from_score(score: dict, out_path: Path) -> None:
    # Mirror stages/export.py:_write_midi's mido usage exactly (read it),
    # but iterate the score's events (pitch, onsetSeconds, offsetSeconds)
    # instead of raw inference NoteEvents, and take tempo from
    # score["parts"][0]["tempoBpm"].
    raise NotImplementedError  # replaced in this task — see step text


def run_rederive_job(job_id: str) -> None:
    session: Session = _SessionLocal()
    storage = LocalStorageClient()
    try:
        job = session.get(TranscriptionJob, job_id)
        if job is None:
            logger.error("rederive job %s not found", job_id)
            return

        newest = (
            session.query(TranscriptionJob)
            .filter(TranscriptionJob.project_id == job.project_id,
                    TranscriptionJob.stage == "rederive")
            .order_by(TranscriptionJob.created_at.desc()).first()
        )
        if newest is not None and newest.id != job.id:
            job.status = "succeeded"
            job.progress = 100
            session.commit()
            return  # superseded — the newer job will re-derive the newer head

        job.status = "running"
        session.commit()

        project = session.get(Project, job.project_id)
        head_id = (project.settings or {}).get("scoreHeadRevisionId")
        revision = session.get(ScoreRevision, head_id) if head_id else None
        if revision is None:
            raise RuntimeError("rederive without a head revision")

        score = _reassign_with_locks(revision.score_json)
        revision.score_json = score
        flag_modified(revision, "score_json")

        with tempfile.TemporaryDirectory() as tmp:
            midi_path = Path(tmp) / "output.mid"
            xml_path = Path(tmp) / "output.musicxml"
            _write_midi_from_score(score, midi_path)
            score_json_to_musicxml(score, xml_path)
            base = f"projects/{project.id}/exports/rev{revision.revision}"
            midi_key, xml_key = f"{base}/output.mid", f"{base}/output.musicxml"
            storage.put_bytes(midi_key, midi_path.read_bytes())
            storage.put_bytes(xml_key, xml_path.read_bytes())

        for export in session.query(Export).filter(Export.project_id == project.id).all():
            export.revision = revision.revision
            export.status = "succeeded"
            export.object_key = midi_key if export.format == "midi" else xml_key
        job.status = "succeeded"
        job.progress = 100
        session.commit()
    except Exception as exc:
        session.rollback()
        job = session.get(TranscriptionJob, job_id)
        if job is not None:
            job.status = "failed"
            job.error_code = "INTERNAL_ERROR"
            job.error_detail = str(exc)[:500]
            session.commit()
        logger.exception("rederive job %s failed", job_id)
    finally:
        session.close()
```

Replace `_write_midi_from_score`'s body per its comment (real mido code mirrored from the export stage). In `apps/api/src/aura_api/queue.py` add, following `enqueue_transcription_job`'s exact pattern:

```python
def enqueue_rederive_job(job_id: str) -> None:
    from aura_worker.rederive import run_rederive_job
    future = _executor.submit(run_rederive_job, job_id)
    ...  # same logging/callback treatment as enqueue_transcription_job
```

(The import lives inside the function only if `queue.py`'s existing style demands it to avoid import cycles — match whatever `enqueue_transcription_job` does.)

- [ ] **Step 4: Run tests to pass, then the full worker + api suites.**

- [ ] **Step 5: Commit** — `feat(worker): coalesced rederive job with lock-aware re-assignment`

---

### Task 4: Edit endpoints + head-aware score resolution

**Files:**
- Create: `apps/api/src/aura_api/routers/edits.py`
- Modify: `apps/api/src/aura_api/routers/scores.py` (head-aware `get_score`)
- Modify: `apps/api/src/aura_api/schemas.py`, `apps/api/src/aura_api/main.py` (include router)
- Create: `apps/api/tests/test_edits_endpoints.py`

**Interfaces:**
- Consumes: T1 `apply_edit`/`EditError`; T3 `enqueue_rederive_job`; existing `_latest_artifact` in `scores.py`; `ScoreRevision`, `Project.settings`.
- Produces (frontend contract, exact):
  - `POST /v1/projects/{id}/edits` body `= one op dict` → `200 {"version": int, "score": {...}, "rederive_job_id": str}`; `404` no project / no transcribed score; `422 {"detail": reason}` invalid op.
  - `POST /v1/projects/{id}/edits/undo` / `/redo` → same 200 shape; `409` at bounds.
  - `POST /v1/projects/{id}/edits/revert` → same 200 shape (head → baseline).
  - `GET /v1/projects/{id}/score` now returns the head revision's score when the head pointer is set; otherwise the assign artifact exactly as today.

- [ ] **Step 1: Write the failing tests** — follow `test_scores_endpoints.py`'s storage-monkeypatch idiom for the storage-dependent parts; seed helper mirrors `_project_with_job` there, plus an `assign` StageArtifact whose blob is a real minimal schema-v4 score (reuse Task 1's `_score()` shape with string/fret set). Scenarios (each an actual test function):
  1. First edit bootstraps a baseline revision from the assign artifact, creates revision N+1 with the edit applied, sets `settings["scoreHeadRevisionId"]`, returns the edited score + a rederive job id whose `GET /v1/jobs/{id}` shows `stage == "rederive"` (monkeypatch `aura_api.routers.edits.enqueue_rederive_job` to a no-op recorder — the queue must NOT actually run in tests).
  2. `GET /score` returns the head revision's JSON after an edit; after `undo` it returns the baseline's; after `redo` the edit again.
  3. `undo` at baseline → 409; `redo` at newest → 409.
  4. Edit-while-rewound truncates: edit A, edit B, undo, edit C → revisions above the old head deleted; `redo` → 409; head score == C's result.
  5. Invalid op (`set_pitch` to 128) → 422 with the `EditError` reason in `detail`, and NO new revision row.
  6. `revert` → head is the baseline revision; score matches the original assign artifact content.

- [ ] **Step 2: Run to verify failure** (404s — router absent).

- [ ] **Step 3: Implement `routers/edits.py`**

```python
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from aura_api.deps import get_db
from aura_api.models import Project, ScoreRevision, TranscriptionJob
from aura_api.queue import enqueue_rederive_job
from aura_api.routers.scores import _latest_artifact
from aura_api.storage import storage_client
from score_schema.edits import EditError, apply_edit

router = APIRouter(tags=["edits"])


def _head_revision(db: Session, project: Project) -> ScoreRevision | None:
    head_id = (project.settings or {}).get("scoreHeadRevisionId")
    return db.get(ScoreRevision, head_id) if head_id else None


def _set_head(db: Session, project: Project, revision: ScoreRevision) -> None:
    settings = dict(project.settings or {})
    settings["scoreHeadRevisionId"] = revision.id
    project.settings = settings
    flag_modified(project, "settings")


def _bootstrap_baseline(db: Session, project: Project) -> ScoreRevision:
    artifact = _latest_artifact(db, project.id, "assign")
    if artifact is None:
        raise HTTPException(status_code=404, detail="no transcribed score yet")
    score = json.loads(storage_client.get_bytes(artifact.object_key))
    top = (
        db.query(ScoreRevision).filter(ScoreRevision.project_id == project.id)
        .order_by(ScoreRevision.revision.desc()).first()
    )
    baseline = ScoreRevision(
        project_id=project.id, parent_id=top.id if top else None,
        revision=(top.revision + 1) if top else 0,
        score_json=score, created_by="baseline",
    )
    db.add(baseline)
    db.flush()
    return baseline


def _start_rederive(db: Session, project: Project) -> TranscriptionJob:
    source = (
        db.query(TranscriptionJob)
        .filter(TranscriptionJob.project_id == project.id)
        .order_by(TranscriptionJob.created_at.desc()).first()
    )
    job = TranscriptionJob(
        project_id=project.id, media_asset_id=source.media_asset_id,
        input_hash=f"rederive-{project.id}-{db.query(TranscriptionJob).count()}",
        status="queued", stage="rederive", progress=0,
    )
    db.add(job)
    db.flush()
    return job


def _respond(db: Session, project: Project, revision: ScoreRevision) -> dict:
    _set_head(db, project, revision)
    job = _start_rederive(db, project)
    db.commit()
    enqueue_rederive_job(job.id)
    return {"version": revision.revision, "score": revision.score_json,
            "rederive_job_id": job.id}


@router.post("/projects/{project_id}/edits")
def apply_project_edit(project_id: str, op: dict, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    head = _head_revision(db, project) or _bootstrap_baseline(db, project)
    try:
        edited = apply_edit(head.score_json, op)
    except EditError as exc:
        raise HTTPException(status_code=422, detail=exc.reason) from exc
    (
        db.query(ScoreRevision)
        .filter(ScoreRevision.project_id == project_id,
                ScoreRevision.revision > head.revision)
        .delete()
    )
    revision = ScoreRevision(
        project_id=project_id, parent_id=head.id, revision=head.revision + 1,
        score_json=edited, created_by="user",
    )
    db.add(revision)
    db.flush()
    return _respond(db, project, revision)


@router.post("/projects/{project_id}/edits/undo")
def undo_edit(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    head = _head_revision(db, project)
    if head is None or head.parent_id is None or head.created_by == "baseline":
        raise HTTPException(status_code=409, detail="nothing to undo")
    parent = db.get(ScoreRevision, head.parent_id)
    return _respond(db, project, parent)


@router.post("/projects/{project_id}/edits/redo")
def redo_edit(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    head = _head_revision(db, project)
    child = (
        db.query(ScoreRevision)
        .filter(ScoreRevision.parent_id == (head.id if head else None))
        .first()
    ) if head else None
    if child is None:
        raise HTTPException(status_code=409, detail="nothing to redo")
    return _respond(db, project, child)


@router.post("/projects/{project_id}/edits/revert")
def revert_edits(project_id: str, db: Session = Depends(get_db)) -> dict:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    baseline = (
        db.query(ScoreRevision)
        .filter(ScoreRevision.project_id == project_id,
                ScoreRevision.created_by == "baseline")
        .order_by(ScoreRevision.revision.desc()).first()
    )
    if baseline is None:
        raise HTTPException(status_code=409, detail="no edits to revert")
    return _respond(db, project, baseline)
```

In `scores.py`'s `get_score`, before the artifact path: resolve `settings["scoreHeadRevisionId"]` → if set and the revision exists, return `revision.score_json` directly (no storage read). Keep the artifact path untouched otherwise. In `main.py`, `app.include_router(edits.router, prefix="/v1")`. Add nothing to `schemas.py` unless FastAPI response validation demands it (plain dict returns match the existing scores router style).

Note on `undo`: the head chain is linear per project (truncation guarantees it), so `parent_id` walking is unambiguous; `redo` finds the unique child. The truncation delete runs BEFORE inserting the new revision so the unique-child property holds.

- [ ] **Step 4: Run tests to pass, then full `apps/api` + `apps/desktop` suites.**

- [ ] **Step 5: Commit** — `feat(api): semantic edit endpoints with revision history and rederive`

---

### Task 5: Frontend api additions + editor store

**Files:**
- Modify: `apps/desktop/web/src/lib/api.ts`, `src/lib/types.ts`
- Create: `apps/desktop/web/src/lib/editor.ts`, `src/lib/editor.test.ts`

**Interfaces:**
- Consumes: T4's endpoints (exact JSON shapes above); existing `api.getJob`.
- Produces (T6/T7 consume): `types.ts` gains `EditOp` (discriminated union mirroring the op table) and `EditResponse {version: number; score: ScoreJson; rederive_job_id: string}`. `api.applyEdit(projectId, op)`, `api.undoEdit(projectId)`, `api.redoEdit(projectId)`, `api.revertEdits(projectId)` — all `Promise<EditResponse>`. `editor` store (module singleton like `playback`): state `{selectedEventId: string | null, score: ScoreJson | null, updating: boolean, canUndo: boolean, canRedo: boolean, error: string | null}` with `select(id)`, `clearSelection()`, `setScore(score)`, `apply(op): Promise<void>` (calls API, updates score immediately, tracks the rederive job via `api.getJob` polling at 500ms until terminal, sets/clears `updating`), `undo()`, `redo()`, `revert()`. `canUndo`/`canRedo` derive from the last response versions + 409 handling (a 409 flips the flag off). Pure export for tests: `isTerminal(status: string): boolean`.

- [ ] **Step 1: Failing store tests (Vitest, fake timers, `vi.mock` the api module)** — apply() updates score before the job resolves; `updating` true until job terminal; a 422 sets `error` and leaves score unchanged; undo 409 flips `canUndo` false; two rapid `apply()` calls serialize (second awaits the first's HTTP, not its rederive polling).
- [ ] **Step 2: Implement; `npm test` green; `npm run check` clean.**
- [ ] **Step 3: Commit** — `feat(web): edit api client and editor store`

---

### Task 6: Click-selection on the notation

**Files:**
- Create: `apps/desktop/web/src/lib/correlate.ts`, `src/lib/correlate.test.ts`
- Modify: `apps/desktop/web/src/components/Notation.svelte`, `ScoreView.svelte`

**Interfaces:**
- Consumes: the cursor-walk technique from `ScoreView.svelte`'s existing `walkNonRestStepIndices` + `timeline.ts` grouping (read both first); OSMD graphical access verified in earlier sub-projects (`GNotesUnderCursor()[i].PositionAndShape.AbsolutePosition`, cursor `reset/next/EndReached`, `NotesUnderCursor()` with per-staff duplicates and `isRest()`).
- Produces: `correlate.ts` pure function `buildEventPositionIndex(walk: StepNoteInfo[], timeline: TimelineEntry[], score: ScoreJson): EventPosition[]` where `StepNoteInfo` is what a one-time cursor walk records per non-rest step (`{step, notes: {pitch, staffId, x, y}[]}`) and `EventPosition = {eventId, x, y, pitch}` — matching each step's distinct pitches to the score events of that step's onset group (dedupe per-staff duplicates by pitch; chords disambiguate by pitch; equal pitches in one chord fall back to document order). Plus `nearestEvent(index: EventPosition[], x, y, maxDistance): string | null`. `Notation.svelte` gains `getEventPositions(): EventPosition[]` (built during the same load-time walk the timeline already does — extend that walk, do NOT add a second walk) and an exported `highlightEvent(eventId | null)` that draws/clears an amber overlay rectangle at the event's position (absolute-positioned div over the OSMD container — do not touch OSMD's SVG internals). Rebuilt on every re-render via the existing `onRerender` callback.
- Click flow: container click → `nearestEvent` → `editor.select(id)`; click on empty space clears. Selection re-highlighted after re-render (positions rebuilt).

- [ ] **Step 1: Failing unit tests for `correlate.ts`** — two-staff guitar duplicates dedupe to one event; chord pitches map to distinct eventIds; equal-pitch chord members resolve by order; `nearestEvent` picks the closest within threshold, null outside.
- [ ] **Step 2: Implement + wire.** OSMD position units: verify how `AbsolutePosition` maps to CSS pixels against the installed typings/source (there is a unit scale — `osmd.zoom` times 10 px per OSMD unit is the historical rule; VERIFY, don't trust this sentence) and encode the real conversion in `Notation.svelte`, keeping `correlate.ts` unit-agnostic.
- [ ] **Step 3: Manual verification in the real app** (rebuild bundled backend first if any backend change landed since last build): click notes in guitar + piano projects — correct note highlights (check via inspector pitch readout once T7 lands, or console log eventId + pitch now); zoom then click still correct. Screenshots → `.superpowers/sdd/2026-08-18-semantic-editing/task-6-screenshots/`.
- [ ] **Step 4: `npm test` + `npm run check` + `npm run build` clean; commit** — `feat(web): click-to-select notes on the score`

---

### Task 7: Inspector, editable facts, keyboard, refresh loop

**Files:**
- Modify: `apps/desktop/web/src/components/Sidebar.svelte` (Inspector section + editable facts), `ScoreView.svelte` (keyboard, refresh-on-rederive, error banner), `Transport.svelte` only if the updating state needs a transport hint (avoid if possible).

**Interfaces:**
- Consumes: `editor` store (T5), selection (T6), existing sidebar patterns/visual language.
- Produces: the complete editing UX per spec §5.2/§5.3/§6.

- [ ] **Step 1: Inspector section** (visible when `editor.selectedEventId` set): pitch stepper showing note name + octave (compute from MIDI; ↑/↓ buttons → `set_pitch`), onset/duration steppers in grid steps (buttons → `move_note`/`set_duration` with the fraction math done via a small helper mirroring `timeline.ts`'s fraction handling), voice read-only, string/fret numeric inputs (guitar) or hand toggle (piano) → `set_fingering`/`set_hand`, lock toggle → `set_locked`, confidence readout, Delete button → `delete_note` + `clearSelection()`. Add-note mini-form (pitch name select + duration select + "Add at selection's measure/beat" or measure-1/beat-0 default when nothing selected) → `add_note`. 422 errors render inline under the offending control from `editor.error`.
- [ ] **Step 2: Editable facts**: key picker (tonic × major/minor list), tempo number input (20-300), meter picker (4/4, 3/4, 6/8) → `set_part_fact`. Undo/Redo/Revert buttons (disabled from `canUndo`/`canRedo`; Revert confirms via a two-click "Revert? / Confirm" pattern, no browser dialogs).
- [ ] **Step 3: Keyboard** on ScoreView (window listener, removed on destroy, ignored when focus is in an input): ↑/↓ semitone, Shift+↑/↓ octave, ←/→ onset ± one grid step, Delete deletes, Ctrl+Z / Ctrl+Shift+Z undo/redo, Escape clears selection.
- [ ] **Step 4: Refresh loop**: when `editor.updating` transitions true→false successfully, re-fetch the MusicXML export text and re-render Notation (reuse the existing load path), restore selection highlight; playback timeline rebuilt from the store's edited score (NOT the refetch — the store score is authoritative between renders). While `updating`: subtle overlay hint on the paper ("Updating notation…", dim, non-blocking). Rederive failure (job failed): dismissible banner with Retry. Mechanism: a failed rederive leaves the head revision's user intent intact, so Retry just needs a fresh rederive of the same head — implement it as `api.applyEdit` with `{"type": "set_locked", "eventId": <any existing event id>, "locked": <its current value>}` (a semantically-null edit that creates a revision identical in intent and triggers a fresh rederive), with a code comment explaining the trick. A cleaner variant — an explicit no-op op type (`{"type": "touch"}`) in `score_schema.edits` plus one test — is acceptable; implementer's choice, document which.
- [ ] **Step 5: Manual verification** (real app, rebuilt backend): full edit session on the guitar project — select, pitch up (notation updates, TAB fret changes after rederive), move a note, delete, add, lock a fingering and change a neighbor (locked survives), tempo change (playback timing shifts), undo/redo/revert; piano: hand override survives rederive. Screenshots each stage → task-7-screenshots/. Verify synth playback reflects an edited pitch immediately (store score drives synth).
- [ ] **Step 6: `npm test` + `npm run check` + `npm run build`; commit** — `feat(web): inspector editing, keyboard shortcuts, live refresh`

---

### Task 8: End-to-end verification + docs

**Files:**
- Modify: `docs/superpowers/SESSION-HANDOFF.md`

- [ ] **Step 1: Rebuild bundled backend** (`bash apps/desktop/build-backend.sh`) — mandatory: Tasks 1-4 changed backend packages.
- [ ] **Step 2: Full journey** (fresh app-data dir, backup/restore the existing one): upload guitar fixture → transcribe → edit session (pitch, move, add, delete, lock+rederive, tempo, undo/redo/revert) → export MusicXML + MIDI and verify the FILES reflect the edits (parse the MusicXML for the edited pitch; open the MIDI with mido and assert the edited note number) → abbreviated piano pass (hand override + undo). Screenshots + command log → task-8 report.
- [ ] **Step 3: Full suites**: `make test`, `cd apps/desktop/web && npm test && npm run check && npm run build`, `cd apps/desktop/src-tauri && cargo build && cargo clippy` (Rust untouched — verify no drift).
- [ ] **Step 4: SESSION-HANDOFF** — sub-project 4 section, honest convention: "8 of 8 tasks implemented and reviewed clean; final whole-branch review pending" (never "DONE" before that review — thrice-established precedent). Note new gotchas (rederive job rows appear as the project's latest job — Home chip flickers during rederive, cosmetic; retry-rederive mechanism; anything discovered).
- [ ] **Step 5: Commit** — `docs: record semantic editing progress`

## Self-Review Notes

- Spec coverage: ops table → T1; DP locks → T2; rederive/coalescing/exports → T3; endpoints/history/head/bounds → T4; store/client → T5; selection/hit-testing → T6; inspector/facts/keyboard/refresh/errors → T7; e2e + docs → T8. Non-goals absent. Spec §4.2 (ScoreRevision + settings head pointer) matches T3/T4 exactly.
- Known cosmetic tradeoff (deliberate, documented in T8): rederive job rows share the TranscriptionJob table, so the Home screen's "latest job" chip reflects a running rederive for ~a second. Accepted — spec §4.3 mandates jobs-endpoint observability without schema changes.
- Type consistency: `EditResponse.rederive_job_id` (snake_case from FastAPI) used verbatim in `types.ts`; `apply_edit` op field names match the spec table and the frontend `EditOp` union; `assign_measure(events, locked=...)` signature consistent across T2/T3.
- The one intentionally-underspecified backend snippet (`_write_midi_from_score` body) is bounded by an explicit mirror instruction to real code in the same repo, not a placeholder.
