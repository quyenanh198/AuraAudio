# Meter Expansion — Design Spec

Date: 2026-08-19
Status: Approved (roadmap item 3, direction chosen by the user: meter expansion)
Predecessors: all four desktop-pivot sub-projects merged; release pipeline ships .deb/.dmg/.msi.

## 1. Context and Goal

AuraAudio's pipeline currently supports exactly two meters, hardcoded in
four places: `structure.py` `METER_CANDIDATES` (detection), `validate.py`
part enum, `edits.py` `_ALLOWED_METERS` (the `set_part_fact` meter
guard), and `Sidebar.svelte` `METER_OPTIONS`. All the underlying math
(measure bucketing, grid steppers, MusicXML measure length) already
parses arbitrary "N/D" strings — the lists are the only gate. Music in
6/8, 2/4, 12/8, 5/4 etc. is forced into 4/4 or 3/4 today.

Goal, per the user's choices:
- **Manually settable meters (edit/validate/export): 10** —
  `2/4, 3/4, 4/4, 5/4, 2/2, 3/8, 6/8, 7/8, 9/8, 12/8`.
- **Auto-detected meters (structure stage): 4** — `4/4, 3/4, 6/8, 2/4`
  (a conservative candidate set; rare meters are user corrections).
- **One source of truth** for both lists in `score_schema`, ending the
  four-way hardcode drift (approach A, chosen over per-package edits).

## 2. Non-Goals

- Mid-piece meter changes (one meter per part, as today).
- Pickup (anacrusis) measures.
- Detecting any meter outside the 4 candidates.
- Free-form N/D input in the UI (curated `<select>` list only).
- Beam-grouping customization (music21 defaults per meter are accepted).

## 3. Single Source of Truth: `score_schema.meters` (new module)

`packages/score_schema/src/score_schema/meters.py`:

- `SUPPORTED_METERS: tuple[str, ...] = ("2/4", "3/4", "4/4", "5/4",
  "2/2", "3/8", "6/8", "7/8", "9/8", "12/8")` — order is the UI display
  order.
- `DETECTABLE_METERS: tuple[str, ...] = ("4/4", "3/4", "6/8", "2/4")` —
  must be a subset of `SUPPORTED_METERS` (asserted by a test).
- `beats_per_measure(meter: str) -> Fraction` — measure length in
  quarter-note beats: `num * 4 / den` (e.g. 4/4→4, 6/8→3, 2/2→4,
  7/8→7/2). This is the existing `edits.beats_per_measure` semantics,
  MOVED here; `edits.py` re-exports/imports it so its callers keep
  working. Raises `ValueError` on strings not in `SUPPORTED_METERS`
  (callers validate first; the helper still refuses garbage).
- `is_compound(meter: str) -> bool` — denominator 8 and numerator
  divisible by 3 (6/8, 9/8, 12/8; NOT 3/8 or 7/8).
- `notated_beats(meter: str) -> int` — felt beats per measure: compound
  → numerator / 3 (6/8→2, 12/8→4); simple → numerator. Used by
  detection scoring and available to future UI work.

Consumers:
- `validate.py`: the part `meter` enum becomes
  `{"enum": list(SUPPORTED_METERS)}`.
- `edits.py`: `_ALLOWED_METERS` deleted; `set_part_fact` validates
  against `SUPPORTED_METERS`; `beats_per_measure` imported from
  `meters`. `_rebucket` is already meter-generic — behavior unchanged.
- Worker `structure.py` / `quantize.py`: import from `score_schema.meters`
  (the worker already depends on score_schema).
- Frontend mirrors the list as a constant (§6) pinned by tests.

Schema note: this widens the accepted enum only. Existing scores
("4/4"/"3/4") remain valid; no score schemaVersion bump — v4 documents
the enum as "the supported-meters list" rather than a frozen pair.

## 4. Detection (`structure.py`)

`METER_CANDIDATES` is replaced by scoring descriptors derived from
`DETECTABLE_METERS`:

- Each candidate scores over the librosa beat-accent sequence exactly as
  today (comb of every `period`-th tracked beat, best offset's mean
  accent minus overall mean).
- Simple meters use `period = notated numerator` as today: 4/4→4,
  3/4→3, 2/4→2.
- 6/8 scores on `period = 6` (librosa's tracker follows the eighth-note
  pulse in compound time at typical tempi) **with a secondary-accent
  term**: the winning offset's comb mean PLUS half the mean accent at
  `offset + 3` positions, distinguishing the 3+3 grouping of 6/8 from
  3/4's flat period-3 accents. The exact weighting (0.5) is a starting
  constant; the fixture tests (§7) pin the required outcomes, and the
  implementer tunes the constant only if a fixture fails.
- Tie-breaking and confidence formula unchanged (margin / total margin;
  `max()` preserves dict order for exact ties, so 4/4 stays first in the
  candidate dict as the default).
- `STAGE_VERSION` bumps 1→2 (cached structure artifacts re-derive).

Risk note: period-2 (2/4) divides period-4 (4/4), so every true-4/4
piece also scores on 2/4's comb. The 4/4 comb hits only downbeats and
therefore scores a higher mean on genuinely 4/4 accents; the 2/4-vs-4/4
fixtures make this required behavior, not hope.

## 5. Quantize + Export

- `quantize.py`: `beats_per_measure = METER_CANDIDATES[structure.meter]`
  becomes `beats_per_measure(structure.meter)` from
  `score_schema.meters` (Fraction). Measure-number arithmetic
  (`onset_beats // beats_per_measure`, silent-measure range emission,
  `_rebucket`-equivalent bucketing) already works on Fractions.
  `GRID_BEATS` (16th-note snap) is unchanged — it is meter-independent.
  `STAGE_VERSION` bumps 3→4.
- `musicxml/export.py`: `_measure_length_ql` already computes
  `num * 4 / den`; music21's `TimeSignature("6/8")` handles notation and
  default beaming. No code change expected — the deliverable is test
  coverage: an export round-trip for EVERY meter in `SUPPORTED_METERS`
  (guitar single-staff + piano grand-staff), asserting the emitted
  `<time>` element and per-measure duration sums. Any failure found is
  fixed in export.py as part of that task.
- MIDI export path is meter-independent (seconds-based) — no change.

## 6. Frontend

- `apps/desktop/web/src/lib/noteEdit.ts` gains
  `export const METER_OPTIONS = [...]` (the 10 meters, same order as
  `SUPPORTED_METERS`) with a comment naming
  `score_schema/meters.py::SUPPORTED_METERS` as the source of truth;
  `Sidebar.svelte` imports it (deleting its local list and the stale
  "only accepts 4/4 and 3/4" comment).
- A Vitest test pins the list contents and order; the Python side pins
  the same list, so a drift on either side fails that side's suite (the
  repo's established mirror-constant pattern, e.g. noteEdit ↔ edits.py).
- `measureLengthWhole` / `stepOnset` are already meter-generic; new unit
  cases cover 6/8 and 7/8 stepping across the measure boundary.
- No new UI: the existing meter `<select>` simply gains options.

## 7. Testing

- `score_schema` unit: all three helpers across all 10 meters (+
  rejection of "13/16", "0/4", "4/3", garbage); DETECTABLE ⊆ SUPPORTED;
  `set_part_fact` accepts all 10 and re-buckets 4/4→6/8→4/4
  round-trip losslessly; validate accepts all 10 in a full score.
- `test_fixtures` (`generate.py`): accent-pattern click-track generators
  for 2/4, 6/8 (strong-weak-weak ×2 eighths), and 3/4, parameterized by
  tempo — synthesized WAVs like the existing fixtures.
- Worker unit: `_detect_meter` on the new fixtures — required outcomes:
  6/8 fixture → "6/8" (not "3/4"), 2/4 fixture → "2/4", existing 4/4 and
  3/4 fixtures keep detecting as before; quantize bucketing for each of
  the 10 meters (silent-measure emission included for 6/8 and 5/4).
- `musicxml`: round-trip per §5.
- Frontend Vitest: METER_OPTIONS pin, steppers per §6.
- e2e: unchanged — the existing edit journey already exercises
  `set_part_fact`; no new journey.

## 8. Global Constraints (inherited, binding)

- Fixed port 8317; fully offline at runtime; no API shape changes; no DB
  migrations; no new endpoints.
- `aura_api.main` untouched; CORS untouched.
- Visual language unchanged (existing select styling).
- Backend tests use unconditional env overrides; Vitest for frontend.
- Work on branch `claude/multi-ai-skills-caveman-7tx5l0` only; no PRs;
  merge to main only after final review.
