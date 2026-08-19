# Meter Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Support 10 manually-settable meters and 4 auto-detected meters, with one source of truth in `score_schema.meters`.

**Architecture:** New `score_schema.meters` module owns `SUPPORTED_METERS` (10) and `DETECTABLE_METERS` (4) plus meter math helpers; `validate.py`, `edits.py`, worker `structure.py`/`quantize.py` consume it; the frontend mirrors the list as a pinned constant. Detection gains 6/8 (secondary-accent scoring) and 2/4.

**Tech Stack:** Python 3.11 / uv workspace / pytest; librosa + numpy (detection); music21 (export); Svelte 5 + Vitest (frontend).

**Spec:** docs/superpowers/specs/2026-08-19-meter-expansion-design.md (read it first — every task argues from it).

## Global Constraints

- Branch `claude/multi-ai-skills-caveman-7tx5l0` ONLY. Never push elsewhere. No PRs. No merges.
- Conventional commits (`feat:`/`fix:`/`test:`/`docs:`); every commit message body ends with the line `Claude-Session: https://claude.ai/code/session_01JPumCJW5ffWtLKhuo9tnfu`. No model identifiers in any committed artifact.
- `SUPPORTED_METERS` order (everywhere, verbatim): `2/4, 3/4, 4/4, 5/4, 2/2, 3/8, 6/8, 7/8, 9/8, 12/8`. `DETECTABLE_METERS` order: `4/4, 3/4, 6/8, 2/4`.
- No API shape changes, no DB migrations, no new endpoints, `aura_api.main` and CORS untouched, port 8317, offline runtime.
- Backend tests set env vars unconditionally (never `setdefault`). Run Python tests via `uv run --package <pkg> pytest ...` from the repo root.
- Worker stage cache: bump `STAGE_VERSION` when a stage's output semantics change (Tasks 3 and 4 do; nothing else does).

---

### Task 1: `score_schema.meters` module + validate/edits migration

**Files:**
- Create: `packages/score_schema/src/score_schema/meters.py`
- Create: `packages/score_schema/tests/test_meters.py`
- Modify: `packages/score_schema/src/score_schema/validate.py` (part meter enum, currently `"meter": {"enum": ["4/4", "3/4"]}`)
- Modify: `packages/score_schema/src/score_schema/edits.py` (delete `_ALLOWED_METERS` at line ~13 and the local `beats_per_measure` at lines ~25-27; import from `meters`)
- Modify: `packages/score_schema/tests/test_edits.py` (extend `set_part_fact` meter cases)

**Interfaces:**
- Produces: `SUPPORTED_METERS: tuple[str, ...]`, `DETECTABLE_METERS: tuple[str, ...]`, `beats_per_measure(meter: str) -> Fraction` (quarter-note beats, e.g. `"6/8"` → `Fraction(3)`), `is_compound(meter: str) -> bool`, `notated_beats(meter: str) -> int`. All later tasks import exactly these names from `score_schema.meters`.
- Consumes: nothing new.

- [ ] **Step 1: Write the failing tests**

```python
# packages/score_schema/tests/test_meters.py
from fractions import Fraction

import pytest

from score_schema.meters import (
    DETECTABLE_METERS,
    SUPPORTED_METERS,
    beats_per_measure,
    is_compound,
    notated_beats,
)

EXPECTED_ORDER = ("2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8")


def test_supported_meters_exact_list_and_order():
    assert SUPPORTED_METERS == EXPECTED_ORDER


def test_detectable_meters_exact_and_subset():
    assert DETECTABLE_METERS == ("4/4", "3/4", "6/8", "2/4")
    assert set(DETECTABLE_METERS) <= set(SUPPORTED_METERS)


@pytest.mark.parametrize(
    ("meter", "beats"),
    [
        ("2/4", Fraction(2)), ("3/4", Fraction(3)), ("4/4", Fraction(4)),
        ("5/4", Fraction(5)), ("2/2", Fraction(4)), ("3/8", Fraction(3, 2)),
        ("6/8", Fraction(3)), ("7/8", Fraction(7, 2)), ("9/8", Fraction(9, 2)),
        ("12/8", Fraction(6)),
    ],
)
def test_beats_per_measure_all_supported(meter, beats):
    assert beats_per_measure(meter) == beats


@pytest.mark.parametrize("bad", ["13/16", "0/4", "4/3", "44", "", "6/8 ", "four/four"])
def test_beats_per_measure_rejects_unsupported(bad):
    with pytest.raises(ValueError):
        beats_per_measure(bad)


@pytest.mark.parametrize(
    ("meter", "compound"),
    [
        ("6/8", True), ("9/8", True), ("12/8", True),
        ("3/8", False), ("7/8", False), ("2/4", False), ("3/4", False),
        ("4/4", False), ("5/4", False), ("2/2", False),
    ],
)
def test_is_compound(meter, compound):
    assert is_compound(meter) is compound


@pytest.mark.parametrize(
    ("meter", "felt"),
    [
        ("6/8", 2), ("9/8", 3), ("12/8", 4),
        ("2/4", 2), ("3/4", 3), ("4/4", 4), ("5/4", 5), ("2/2", 2),
        ("3/8", 3), ("7/8", 7),
    ],
)
def test_notated_beats(meter, felt):
    assert notated_beats(meter) == felt
```

- [ ] **Step 2: Run tests, verify they fail**

Run: `uv run --package score-schema pytest packages/score_schema/tests/test_meters.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'score_schema.meters'`
(If the package name differs, check `packages/score_schema/pyproject.toml` `[project].name` and use that after `--package`.)

- [ ] **Step 3: Implement the module**

```python
# packages/score_schema/src/score_schema/meters.py
"""Single source of truth for supported meters and meter math.

Mirrored by the frontend's METER_OPTIONS in
apps/desktop/web/src/lib/noteEdit.ts — both sides pin the list with
tests, so a drift on either side fails that side's suite.
"""
from __future__ import annotations

from fractions import Fraction

SUPPORTED_METERS: tuple[str, ...] = (
    "2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8",
)

DETECTABLE_METERS: tuple[str, ...] = ("4/4", "3/4", "6/8", "2/4")


def beats_per_measure(meter: str) -> Fraction:
    """Measure length in quarter-note beats: num * 4 / den.

    Only meters in SUPPORTED_METERS are accepted; callers that surface
    user input validate first and turn ValueError into their own error.
    """
    if meter not in SUPPORTED_METERS:
        raise ValueError(f"unsupported meter: {meter!r}")
    num, den = meter.split("/")
    return Fraction(int(num) * 4, int(den))


def is_compound(meter: str) -> bool:
    """Compound meters group eighth notes in threes (6/8, 9/8, 12/8)."""
    if meter not in SUPPORTED_METERS:
        raise ValueError(f"unsupported meter: {meter!r}")
    num, den = meter.split("/")
    return int(den) == 8 and int(num) % 3 == 0


def notated_beats(meter: str) -> int:
    """Felt beats per measure: compound → numerator/3, simple → numerator."""
    num = int(meter.split("/")[0])
    return num // 3 if is_compound(meter) else num
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `uv run --package score-schema pytest packages/score_schema/tests/test_meters.py -v`
Expected: PASS (all).

- [ ] **Step 5: Migrate validate.py and edits.py**

In `validate.py`, add `from score_schema.meters import SUPPORTED_METERS` and change the part meter enum line (currently `"meter": {"enum": ["4/4", "3/4"]},`) to:

```python
                    "meter": {"enum": list(SUPPORTED_METERS)},
```

In `edits.py`:
- Delete the `_ALLOWED_METERS = ("4/4", "3/4")` line.
- Delete the local `def beats_per_measure(meter: str) -> Fraction:` (3 lines) and add `from score_schema.meters import SUPPORTED_METERS, beats_per_measure` to the imports. All existing callsites (`_rebucket`, onset guards) keep the same name.
- In the `set_part_fact` meter branch, replace `if value not in _ALLOWED_METERS: raise EditError(f"meter must be one of {_ALLOWED_METERS}")` with:

```python
            if value not in SUPPORTED_METERS:
                raise EditError(f"meter must be one of {SUPPORTED_METERS}")
```

- [ ] **Step 6: Extend edits tests**

In `packages/score_schema/tests/test_edits.py`, find the existing `set_part_fact` meter tests and add (adapting the existing helper that builds a valid score — reuse whatever fixture/builder the file already uses; do NOT invent a new score builder):

```python
@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_set_part_fact_accepts_every_supported_meter(meter, base_score):
    result = apply_edit(base_score, {"type": "set_part_fact", "field": "meter", "value": meter})
    assert result["parts"][0]["meter"] == meter


def test_set_part_fact_rejects_unsupported_meter(base_score):
    with pytest.raises(EditError):
        apply_edit(base_score, {"type": "set_part_fact", "field": "meter", "value": "13/16"})


def test_meter_rebucket_round_trip_4_4_to_6_8_and_back(base_score):
    to_68 = apply_edit(base_score, {"type": "set_part_fact", "field": "meter", "value": "6/8"})
    back = apply_edit(to_68, {"type": "set_part_fact", "field": "meter", "value": "4/4"})
    orig_events = [
        (e["id"], e["notatedOnset"], e["notatedDuration"])
        for m in base_score["parts"][0]["measures"] for e in m["events"]
    ]
    back_events = [
        (e["id"], e["notatedOnset"], e["notatedDuration"])
        for m in back["parts"][0]["measures"] for e in m["events"]
    ]
    assert back_events == orig_events
```

(`base_score` = the file's existing valid-score fixture; if it's named differently, use that name. Import `SUPPORTED_METERS` from `score_schema.meters` at the top.)

- [ ] **Step 7: Run the full score_schema suite**

Run: `uv run --package score-schema pytest packages/score_schema/tests -v`
Expected: PASS. If the round-trip test fails because `_rebucket` merges trailing partial measures, that is a REAL finding — investigate `_rebucket` (edits.py:81-…) before touching the test; the spec requires lossless round-trip.

- [ ] **Step 8: Commit**

```bash
git add packages/score_schema
git commit -m "feat: score_schema.meters single source of truth for supported meters"
```

---

### Task 2: test_fixtures accent click-track generators

**Files:**
- Modify: `packages/test_fixtures/src/test_fixtures/generate.py`
- Test: `packages/test_fixtures/tests/` (follow the existing test layout — look at how current generators are tested and mirror it)

**Interfaces:**
- Consumes: existing WAV-synthesis helpers in `generate.py` (read the module first; reuse its click/tone primitives and file-writing conventions).
- Produces: `generate_metered_clicks(meter: str, tempo_bpm: float, measures: int, path: Path) -> Path` — a WAV of accent-patterned clicks: downbeat click loud (amplitude 1.0), other clicks soft (amplitude 0.4). Grid: for simple meters one click per quarter-note beat; for compound (6/8) one click per EIGHTH note with loud clicks on eighths 0 and 3 of each measure (0 louder than 3: 1.0 vs 0.7) and soft (0.4) elsewhere. Task 3's detection tests call this exact signature.

- [ ] **Step 1: Read `generate.py` end to end** — understand the existing synthesis helpers (sample rate, click synthesis, how fixtures write WAVs) so the new generator reuses them.

- [ ] **Step 2: Write the failing test** (in the package's existing test file for generators)

```python
def test_generate_metered_clicks_produces_expected_length(tmp_path):
    from test_fixtures.generate import generate_metered_clicks

    path = generate_metered_clicks("6/8", tempo_bpm=120.0, measures=4, path=tmp_path / "m68.wav")
    assert path.exists()
    import soundfile as sf  # or the reader generate.py already uses

    data, sr = sf.read(path)
    # 6/8 at 120bpm (quarter = 0.5s): measure = 3 quarter beats = 1.5s; 4 measures = 6s
    assert abs(len(data) / sr - 6.0) < 0.1


def test_generate_metered_clicks_rejects_unsupported_meter(tmp_path):
    from test_fixtures.generate import generate_metered_clicks
    import pytest

    with pytest.raises(ValueError):
        generate_metered_clicks("13/16", tempo_bpm=120.0, measures=2, path=tmp_path / "x.wav")
```

(If `soundfile` isn't already a dependency, use whatever audio reader the existing fixture tests use — do not add a new dependency.)

- [ ] **Step 3: Run tests, verify failure** — `uv run --package test-fixtures pytest packages/test_fixtures/tests -v -k metered` → FAIL (no such function).

- [ ] **Step 4: Implement `generate_metered_clicks`**

Reuse the module's click primitive. Logic:

```python
def generate_metered_clicks(meter: str, tempo_bpm: float, measures: int, path: Path) -> Path:
    from score_schema.meters import beats_per_measure, is_compound

    seconds_per_quarter = 60.0 / tempo_bpm
    if is_compound(meter):
        # one click per eighth; loud on eighths 0 and 3 of each measure
        eighths = int(meter.split("/")[0])
        step_s = seconds_per_quarter / 2.0
        amps = [1.0 if i == 0 else 0.7 if i == 3 else 0.4 for i in range(eighths)]
    else:
        beats = int(beats_per_measure(meter))  # all simple SUPPORTED meters with den 4 or 2 are integral
        step_s = seconds_per_quarter * (float(beats_per_measure(meter)) / beats)
        amps = [1.0 if i == 0 else 0.4 for i in range(beats)]
    # synthesize: for each measure, for each grid slot, add a click at
    # measure_start + i*step_s scaled by amps[i]; write WAV via the
    # module's existing writer at its existing sample rate.
    ...
```

Fill the synthesis with the module's real primitives (this pseudocode fixes the accent CONTRACT; the audio plumbing follows the file's own conventions). Note 7/8 etc. are not needed by detection tests but must not crash: the simple-meter branch already handles any supported meter with integral quarter beats; for 7/8 and 3/8 (`beats_per_measure` = 7/2 and 3/2, non-integral) click per EIGHTH instead: `step_s = seconds_per_quarter / 2`, `amps = [1.0] + [0.4] * (numerator - 1)`. Guard: `beats_per_measure(meter)` raises ValueError for unsupported strings — that satisfies the rejection test for free.

- [ ] **Step 5: Run tests, verify pass** — same command → PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/test_fixtures
git commit -m "feat: metered click-track fixture generator for meter detection tests"
```

---

### Task 3: Detection — 6/8 and 2/4 candidates in `structure.py`

**Files:**
- Modify: `workers/transcription/src/aura_worker/stages/structure.py` (lines 14-16 constants, `_detect_meter` at lines 59-79)
- Test: `workers/transcription/tests/test_structure.py` (or wherever `_detect_meter` tests live — find them with `grep -rn _detect_meter workers/transcription/tests/`)

**Interfaces:**
- Consumes: `score_schema.meters.DETECTABLE_METERS`, `notated_beats`, `is_compound`; `test_fixtures.generate.generate_metered_clicks(meter, tempo_bpm, measures, path)` from Task 2.
- Produces: `_detect_meter(y, sr, beat_times) -> tuple[str, float]` unchanged in signature; `METER_CANDIDATES` is REMOVED from this module (Task 4 depends on that removal — quantize must stop importing it in the same PR, but each task's suite must pass at its own commit, so this task keeps a deprecation alias `METER_CANDIDATES = {m: int(notated_beats(m)) for m in ("4/4", "3/4")}` ONLY if quantize still imports it at this commit; Task 4 deletes the alias. Check `grep -rn METER_CANDIDATES workers/` and keep the alias iff there are remaining importers.)
- `STAGE_VERSION` 1→2.

- [ ] **Step 1: Write the failing tests**

```python
# in the worker's structure test file, following its existing test style
import numpy as np
import librosa

from aura_worker.stages import structure
from test_fixtures.generate import generate_metered_clicks


def _detect_on_fixture(tmp_path, meter, tempo=120.0, measures=8):
    path = generate_metered_clicks(meter, tempo_bpm=tempo, measures=measures, path=tmp_path / "clip.wav")
    y, sr = librosa.load(str(path), sr=None)
    _, beat_times = structure._detect_tempo_and_beats(y, sr)
    detected, confidence = structure._detect_meter(y, sr, beat_times)
    return detected, confidence


def test_detects_6_8_not_3_4(tmp_path):
    detected, confidence = _detect_on_fixture(tmp_path, "6/8")
    assert detected == "6/8"
    assert 0.0 <= confidence <= 1.0


def test_detects_2_4(tmp_path):
    detected, _ = _detect_on_fixture(tmp_path, "2/4")
    assert detected == "2/4"


def test_still_detects_4_4(tmp_path):
    detected, _ = _detect_on_fixture(tmp_path, "4/4")
    assert detected == "4/4"


def test_still_detects_3_4(tmp_path):
    detected, _ = _detect_on_fixture(tmp_path, "3/4")
    assert detected == "3/4"


def test_stage_version_bumped():
    assert structure.STAGE_VERSION == 2
```

Keep any EXISTING detection tests passing unmodified — they are the regression net. If an existing test hardcodes `METER_CANDIDATES`, update it to the new scoring-descriptor structure and note that in the report.

- [ ] **Step 2: Run tests, verify the new ones fail** — `uv run --package aura-worker pytest workers/transcription/tests -v -k "detect or stage_version"` → new tests FAIL (6/8 and 2/4 not candidates; STAGE_VERSION 1).

- [ ] **Step 3: Implement scoring**

Replace `METER_CANDIDATES` and `_detect_meter`'s comb loop:

```python
from score_schema.meters import DETECTABLE_METERS, is_compound, notated_beats

STAGE_VERSION = 2
SECONDARY_ACCENT_WEIGHT = 0.5


def _comb_score(accents: np.ndarray, period: int, offset: int) -> float:
    comb = accents[offset::period]
    return float(np.mean(comb)) if len(comb) >= 1 else 0.0


def _detect_meter(y, sr, beat_times: np.ndarray) -> tuple[str, float]:
    import librosa

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    onset_sr = sr / 512.0
    accents = np.array([_accent_at(onset_env, onset_sr, t) for t in beat_times])
    overall_mean = float(np.mean(accents)) if len(accents) else 0.0

    margins: dict[str, float] = {}
    for meter_name in DETECTABLE_METERS:
        if is_compound(meter_name):
            period = int(meter_name.split("/")[0])  # 6 tracked eighths for 6/8
            best = 0.0
            for offset in range(period):
                primary = _comb_score(accents, period, offset)
                secondary = _comb_score(accents, period, (offset + period // 2) % period)
                score = primary + SECONDARY_ACCENT_WEIGHT * secondary
                best = max(best, score)
            margins[meter_name] = best - (1.0 + SECONDARY_ACCENT_WEIGHT) * overall_mean
        else:
            period = notated_beats(meter_name)
            scores = [_comb_score(accents, period, o) for o in range(period)]
            margins[meter_name] = (max(scores) - overall_mean) if scores else 0.0

    best_meter = max(margins, key=margins.get)
    total_margin = sum(max(m, 0.0) for m in margins.values())
    confidence = (max(margins[best_meter], 0.0) / total_margin) if total_margin > 0 else 0.5
    return best_meter, float(np.clip(confidence, 0.0, 1.0))
```

Notes: `max(margins, key=...)` keeps insertion order on ties — `DETECTABLE_METERS` starts with 4/4, preserving the 4/4-default tie-break. The compound margin subtracts `(1 + w) * overall_mean` so its scale matches the simple-meter margins. If a fixture test fails, tune `SECONDARY_ACCENT_WEIGHT` (try 0.3-0.8) BEFORE touching test expectations, and record the final value + why in the task report.

- [ ] **Step 4: Run the worker's full detection/structure suite** — `uv run --package aura-worker pytest workers/transcription/tests -v -k structure` → PASS including pre-existing tests.

- [ ] **Step 5: Commit**

```bash
git add workers/transcription
git commit -m "feat: detect 6/8 and 2/4 meters in structure stage"
```

---

### Task 4: Quantize — meter-generic measure math

**Files:**
- Modify: `workers/transcription/src/aura_worker/stages/quantize.py` (import at line 11, `beats_per_measure` at line 36, STAGE_VERSION at line 15)
- Modify: `workers/transcription/src/aura_worker/stages/structure.py` (delete the `METER_CANDIDATES` deprecation alias if Task 3 left one)
- Test: the worker's quantize test file (find via `grep -rln quantize workers/transcription/tests/`)

**Interfaces:**
- Consumes: `score_schema.meters.beats_per_measure` (Fraction quarter-beats).
- Produces: quantize output unchanged in shape; measure bucketing correct for all 10 supported meters; `STAGE_VERSION` 3→4.

- [ ] **Step 1: Write the failing tests**

```python
# in the worker's quantize test file, using its existing helpers for
# building NoteEvent lists and StructureResult (reuse, don't reinvent)
import pytest
from fractions import Fraction

from score_schema.meters import SUPPORTED_METERS, beats_per_measure


@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_bucketing_matches_meter_length(meter, quantize_harness):
    # one note exactly at the start of what must be measure 2
    bpm = beats_per_measure(meter)
    onset_s = float(bpm) * 0.5  # tempo 120 → quarter = 0.5s → measure = bpm*0.5 s
    result = quantize_harness(meter=meter, tempo_bpm=120.0, notes_at_seconds=[onset_s])
    part = result["parts"][0]
    numbers = [m["number"] for m in part["measures"] if m["events"]]
    assert numbers == [2]
    event = next(m for m in part["measures"] if m["events"])["events"][0]
    assert event["notatedOnset"] == "0/1"


def test_silent_measures_emitted_for_6_8(quantize_harness):
    # note in measure 3 → measures 1..3 all present, 1-2 empty
    onset_s = float(beats_per_measure("6/8")) * 0.5 * 2
    result = quantize_harness(meter="6/8", tempo_bpm=120.0, notes_at_seconds=[onset_s])
    part = result["parts"][0]
    assert [m["number"] for m in part["measures"]] == [1, 2, 3]
    assert part["measures"][0]["events"] == [] and part["measures"][1]["events"] == []


def test_stage_version_bumped():
    from aura_worker.stages import quantize
    assert quantize.STAGE_VERSION == 4
```

`quantize_harness`: whatever the existing quantize tests use to invoke `run()` with a fake ctx/storage — if there's no reusable helper, extract one from an existing test into a fixture in the same file (mechanical extraction, no behavior change). If the existing tests build `StructureResult(meter="4/4", ...)` inline, the harness parameterizes that meter.

- [ ] **Step 2: Run, verify failures** — `uv run --package aura-worker pytest workers/transcription/tests -v -k quantize` → new tests FAIL (`KeyError` on non-candidate meters or STAGE_VERSION mismatch).

- [ ] **Step 3: Implement**

In `quantize.py`:
- Replace `from aura_worker.stages.structure import METER_CANDIDATES, StructureResult` with `from aura_worker.stages.structure import StructureResult` and add `from score_schema.meters import beats_per_measure as meter_beats`.
- Line 36: `beats_per_measure = METER_CANDIDATES[structure.meter]` → `beats_per_measure = meter_beats(structure.meter)`.
- `STAGE_VERSION = 4`.
- Check every use of `beats_per_measure` downstream in the file (measure_number floor-div, onset_within_measure, the silent-measure 1..max range emission) still type-checks with Fraction — `int(onset_beats // beats_per_measure)` works with Fractions; fix anything that assumed int (e.g. formatting) minimally.
- In `structure.py`, delete the deprecation alias if present; `grep -rn METER_CANDIDATES` across the repo must come back empty.

- [ ] **Step 4: Run the full worker suite** — `uv run --package aura-worker pytest workers/transcription/tests -v` → PASS (including rederive tests, which exercise `assign_measure` paths that consume quantize output).

- [ ] **Step 5: Commit**

```bash
git add workers/transcription
git commit -m "feat: meter-generic measure bucketing in quantize stage"
```

---

### Task 5: MusicXML export round-trip for all 10 meters

**Files:**
- Test: `packages/musicxml/tests/test_export_meters.py` (new)
- Modify (only if a test exposes a real defect): `packages/musicxml/src/musicxml/export.py`

**Interfaces:**
- Consumes: `score_schema.meters.SUPPORTED_METERS`, the exporter's public entry point (read `export.py` for its name/signature — the existing tests show how to build a minimal score dict for guitar and piano).

- [ ] **Step 1: Write the tests**

```python
# packages/musicxml/tests/test_export_meters.py
import pytest
from music21 import converter

from score_schema.meters import SUPPORTED_METERS

# Build the minimal valid score dicts the same way the existing export
# tests do (import/reuse their builder helpers if they have any; else
# copy the smallest existing fixture literal and parameterize its meter).


@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_time_signature_round_trips_guitar(meter, guitar_score_builder):
    score = guitar_score_builder(meter=meter)
    xml = export_music_xml(score)  # the module's real entry point name
    parsed = converter.parse(xml)
    ts = parsed.recurse().getElementsByClass("TimeSignature")[0]
    assert ts.ratioString == meter


@pytest.mark.parametrize("meter", SUPPORTED_METERS)
def test_time_signature_round_trips_piano(meter, piano_score_builder):
    score = piano_score_builder(meter=meter)
    xml = export_music_xml(score)
    parsed = converter.parse(xml)
    signatures = {t.ratioString for t in parsed.recurse().getElementsByClass("TimeSignature")}
    assert signatures == {meter}
```

Adapt names to the module's real API (the existing test file is the authority). The builders must place at least one note so measures render.

- [ ] **Step 2: Run** — `uv run --package musicxml pytest packages/musicxml/tests/test_export_meters.py -v`. Expected: PASS if `_measure_length_ql` is as generic as the spec believes. Any failure is a real export defect: fix it in `export.py` minimally (likely suspects: `2/2` `ratioString` normalization by music21 — if music21 reports `"2/2"` as cut time `ratioString == "2/2"` this passes; if it normalizes differently, assert on `ts.numerator/ts.denominator` instead and note it).

- [ ] **Step 3: Run the whole musicxml suite** — `uv run --package musicxml pytest packages/musicxml/tests -v` → PASS.

- [ ] **Step 4: Commit**

```bash
git add packages/musicxml
git commit -m "test: musicxml export round-trip for all supported meters"
```

---

### Task 6: Frontend — METER_OPTIONS mirror + steppers

**Files:**
- Modify: `apps/desktop/web/src/lib/noteEdit.ts` (add exported constant)
- Modify: `apps/desktop/web/src/components/Sidebar.svelte` (delete local `METER_OPTIONS` at line ~214 and the stale lines ~210-213 comment; import from noteEdit)
- Test: `apps/desktop/web/src/lib/noteEdit.test.ts`

**Interfaces:**
- Consumes: nothing new from backend (mirror by convention).
- Produces: `export const METER_OPTIONS: readonly string[]` in `noteEdit.ts`.

- [ ] **Step 1: Write the failing tests** (append to `noteEdit.test.ts`)

```typescript
import { METER_OPTIONS, measureLengthWhole, stepOnset } from "./noteEdit";

test("METER_OPTIONS mirrors score_schema.meters.SUPPORTED_METERS exactly", () => {
  expect(METER_OPTIONS).toEqual([
    "2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8",
  ]);
});

test("stepOnset wraps within a 6/8 measure boundary", () => {
  // 6/8 measure = 6/8 whole note; last 16th-grid onset is 5/8 + step below 6/8
  const atEnd = stepOnset("5/8", 1, "6/8");
  expect(measureLengthWhole("6/8").toString()).toBe("3/4"); // 6/8 reduces to 3/4 whole notes
  expect(atEnd).not.toBeNull();
});

test("stepOnset clamps at 7/8 measure end", () => {
  const stepped = stepOnset("0/1", -1, "7/8");
  expect(stepped).toBe("0/1"); // existing clamp-at-zero behavior
});
```

IMPORTANT: before writing assertions, read `stepOnset`'s actual contract in `noteEdit.ts` (clamp vs wrap vs null at boundaries) and assert its REAL behavior for the new meters — the cases above fix which scenarios must be covered, not the exact expected values; derive those from the implementation's documented contract (and existing 4/4 tests) so the tests document reality. The `measureLengthWhole("6/8") === 3/4` reduction assertion is exact (Fraction reduces 6/8), and METER_OPTIONS content/order is exact per the Global Constraints.

- [ ] **Step 2: Run** — `cd apps/desktop/web && npx vitest run src/lib/noteEdit.test.ts` → new tests FAIL (no METER_OPTIONS export).

- [ ] **Step 3: Implement**

In `noteEdit.ts`:

```typescript
/**
 * Mirror of score_schema/meters.py::SUPPORTED_METERS — same values, same
 * order. Both sides pin this list with tests; change them together.
 */
export const METER_OPTIONS = [
  "2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8",
] as const;
```

In `Sidebar.svelte`: delete the local `const METER_OPTIONS = ["4/4", "3/4"];` and its stale comment block ("only accepts 4/4 and 3/4"); add `METER_OPTIONS` to the existing import from `../lib/noteEdit`. The `<select>` markup is unchanged.

- [ ] **Step 4: Run the full frontend suite** — `cd apps/desktop/web && npx vitest run` → PASS; `npm run check` (svelte-check) if the repo has it in package.json scripts → clean.

- [ ] **Step 5: Commit**

```bash
git add apps/desktop/web
git commit -m "feat: expose all supported meters in the inspector meter picker"
```

---

### Task 7: Full workspace verification + docs

**Files:**
- Modify: `docs/superpowers/SESSION-HANDOFF.md` (roadmap item 3 status)

**Interfaces:** none new — this task proves the whole feature.

- [ ] **Step 1: Full test sweep**

```bash
cd /home/user/AuraAudio
uv run --package score-schema pytest packages/score_schema/tests -q
uv run --package test-fixtures pytest packages/test_fixtures/tests -q
uv run --package musicxml pytest packages/musicxml/tests -q
uv run --package aura-worker pytest workers/transcription/tests -q
uv run --package aura-api pytest apps/api/tests apps/desktop/tests -q
cd apps/desktop/web && npx vitest run
```

Every suite green. Any failure: fix it (it is integration fallout from Tasks 1-6, in scope).

- [ ] **Step 2: End-to-end sanity via the API integration test** — run the repo's existing e2e/integration pytest (find it: `grep -rln "transcrib" apps/api/tests | head -3` or the Makefile's test target) to confirm a full pipeline run still produces a valid score with the new validate enum.

- [ ] **Step 3: Update SESSION-HANDOFF** — roadmap item 3 section: meter expansion delivered (10 supported / 4 detectable, source of truth `score_schema.meters`, STAGE_VERSION bumps 2 and 4, frontend mirror pattern), plus any gotchas discovered (e.g. final SECONDARY_ACCENT_WEIGHT value and why).

- [ ] **Step 4: Commit + push**

```bash
git add docs/superpowers/SESSION-HANDOFF.md
git commit -m "docs: record meter expansion completion in session handoff"
git push -u origin claude/multi-ai-skills-caveman-7tx5l0
```
