# Beat, Meter, and Key Detection — Design

## Context

Phase 1 (the transcription vertical slice, merged to `main`) produces a canonical score by quantizing raw note events to a **hardcoded** 120 BPM / 4/4 grid — regardless of what the audio actually contains. `packages/musicxml/src/musicxml/export.py` compounds this: it hardcodes `TimeSignature("4/4")` and `MetronomeMark(number=120)` on every export, ignoring the score entirely. This sub-project (the first slice of ARCHITECTURE.md §10 Phase 2, item 1) replaces the hardcoded assumption with real per-clip detection of tempo, meter, and key, and threads the detected values through quantization and MusicXML export.

This is scoped as one sub-project out of five identified for Phase 2 (the others — guitar/piano assignment, the web client, PDF rendering, and the offline benchmark pipeline — are out of scope here and get their own specs).

## Goal

Given a short solo clip's raw note events and normalized audio, detect:
- a single global tempo (BPM) for the whole clip,
- the best-fit time signature from a fixed candidate set,
- the best-fit major/minor key,

each with a confidence score, and use those values (instead of hardcoded constants) to quantize notes and render MusicXML with a correct time signature, tempo marking, and key signature.

## Explicit Non-Goals (deferred to later sub-projects or phases)

- **Time-varying tempo.** One BPM per clip, not a tempo curve. Real recordings drift, but a global estimate is a large accuracy improvement over a hardcoded constant at a fraction of the implementation and testing cost; time-varying detection is a natural follow-up once this ships and its failure modes on real material are known.
- **Meters outside {4/4, 3/4, 6/8, 2/4}.** Covers the overwhelming majority of guitar/piano material in the Phase-1 fixture domain (popular, folk, rock). Asymmetric/compound meters beyond 6/8 are out of scope.
- **True multi-voice notation.** Notes sharing an onset become a chord in voice 1. Notes that overlap with *different* onset/offset and genuinely can't share a voice are trimmed to fit and a warning is logged on the `StageArtifact.metrics`, rather than split into independent voices.
- **Triplet/tuplet detection.** Quantization stays on a straight 16th-note grid.
- **Any correction UI.** There is no web client yet (Phase 3+). Low-confidence results are stored with their confidence value, not surfaced for correction — that's a later phase's concern once an editing surface exists.
- **Schema migration tooling for the v1 → v2 score schema bump.** No production data exists yet (pre-launch). The version bump is a breaking change to the canonical score schema, accepted as such; migration tooling is deferred until real persisted scores exist.

## Architecture

A new worker stage, `structure`, runs between `inference` and `quantize`:

```
probe -> normalize -> inference -> structure -> quantize -> export
```

`structure` consumes the normalized audio and the raw note events, and produces tempo/meter/key with confidences. `quantize` consumes that output instead of the hardcoded `BPM = 120` / `GRID_BEATS` constants it uses today. `musicxml/export.py` reads tempo/meter/key from the score instead of hardcoding them.

### Library choice

| Concern | Choice | Why |
|---|---|---|
| Tempo + beat times | `librosa.beat.beat_track` | Already transitively installed (`basic-pitch` pulls `librosa` via its `resampy`/`audioread` chain) — zero new dependency weight. Well-tested, standard approach. |
| Meter | Custom scorer on top of `librosa.onset.onset_strength` | No library gives meter directly. Score each of the 4 candidates by how well period-aligned beat groups line up with onset-strength peaks at the group's first position (downbeat emphasis); pick the argmax, normalize scores to a confidence. |
| Key | `music21`'s built-in key analysis (`Stream.analyze('key')`, Krumhansl-Schmuckler) | Already a direct dependency of `packages/musicxml`. Returns a `Key` object exposing `.correlationCoefficient`, usable directly as a confidence proxy. |

**Rejected:** `madmom` (more accurate joint beat/downbeat/meter model, but unmaintained with brittle old-numpy/Cython build pins — too much install risk for the accuracy gain at this stage) and a hand-rolled DSP tempo/beat estimator (reinvents a solved problem `librosa` already gives us for free, worse accuracy, no dependency savings since `librosa` is already in the tree via `basic-pitch`).

### Data flow

1. `structure.run(ctx, normalized_path, notes)`:
   - Load audio via `librosa.load` (already 22.05kHz mono from the `normalize` stage — no resampling needed).
   - `tempo, beat_frames = librosa.beat.beat_track(...)`; convert to `beat_times`.
   - Tempo confidence: `1 - clip(stddev(inter_beat_intervals) / mean(inter_beat_intervals), 0, 1)` — a simple, deterministic proxy for how metronomic the detected beat grid is (librosa doesn't expose a native confidence value).
   - Meter: score each candidate against `onset_strength`, pick best fit, normalize scores across candidates to get a confidence.
   - Key: build a `music21.stream.Stream` from the note pitches (rhythm-agnostic), call `.analyze('key')`, read `.tonic.name` / `.mode` / `.correlationCoefficient` (clipped to `[0, 1]`).
   - Cache via the existing `find_cached_artifact`/`save_artifact` pattern (stage `"structure"`, version 1) — same resume-on-retry behavior every other stage already has.
   - If `beat_track` returns no usable beats (near-silent or too-short clip), raise `JobFailure(JobErrorCode.MODEL_FAILED, ...)` — reuses the existing error code, no new one needed.
2. `quantize.run` takes the `StructureResult` (tempo, meter, key) as a new parameter, replacing its hardcoded `BPM`/`GRID_BEATS` module constants with values derived from it.
3. `export.run` passes the score's tempo/meter/key through to `musicxml.export.score_json_to_musicxml`, which builds `TimeSignature(meter)`, `MetronomeMark(number=tempo_bpm)`, and a `Key` object instead of hardcoding them.

### Canonical score schema (v2)

`schemaVersion` bumps from `1` to `2`. Each part gains:

```json
{
  "instrument": "guitar",
  "tempoBpm": 128.0,
  "meter": "4/4",
  "key": "C major",
  "confidence": {"tempo": 0.92, "meter": 0.81, "key": 0.67},
  "measures": [ ... ]
}
```

- `meter` is one of `"4/4"`, `"3/4"`, `"6/8"`, `"2/4"`.
- `key` is `"<tonic> major"` or `"<tonic> minor"` (e.g. `"C major"`, `"A minor"`), matching `music21`'s naming so it round-trips directly into `music21.key.Key(...)`.
- Existing per-event fields (`notatedOnset`, `notatedDuration` as whole-note-fraction strings) are **unchanged** — this representation is already meter-agnostic, since it expresses position/duration as a fraction of a whole note rather than in beats. A fixed 1/16-whole-note grid applies uniformly across all four candidate meters (6/8's natural eighth-note subdivision is still exactly 1/16 of a whole note), so no meter-specific quantization branching is needed. Measure length in whole-note units becomes meter-dependent instead of always being `1` (4/4 → `1`, 3/4 → `3/4`, 6/8 → `3/4`, 2/4 → `1/2`), which is the only place meter enters the quantizer's math.

### Enharmonic spelling

Notes are spelled using `music21`'s key-aware pitch spelling once the part's `Key` is known — a pitch class like MIDI 66 spells as D♯ in a key where D is the more natural scale step and as E♭ where E-flat fits better, rather than Phase 1's arbitrary default. Exact `music21` API surface (`pitch.Pitch.getEnharmonic()` vs. building notes inside a keyed `Stream` and letting `makeNotation` resolve spelling) is an implementation-time decision, not fixed here.

## Testing

Phase 1's synthetic fixture (four notes 0.5s apart) has no strong periodic pulse — not enough signal for `beat_track` to lock onto reliably, and no tonal center for key analysis to find. Two new fixtures are needed in `packages/test_fixtures`:

- `write_metronome_pulse_wav(path, bpm, meter, duration_s)` — measured clicks (strong on beat 1, weaker elsewhere) at an exact known BPM/meter, so tempo can be asserted within a tight tolerance (±2 BPM) and the meter scorer can be asserted to pick the correct candidate.
- `write_diatonic_melody_wav(path, key, duration_s)` — a short scale/arpeggio in a known key, so key analysis can be asserted against ground truth instead of merely "didn't crash."

Coverage needed:
- `structure.py`: known-tempo fixture → tempo within tolerance; known-meter fixture → correct candidate wins; known-key fixture → correct tonic/mode; a beats-not-found case → `JobFailure(MODEL_FAILED)`; a cache-resume test matching the existing stage pattern.
- `quantize.py`: existing tests updated to pass a `StructureResult` instead of relying on module constants; new tests for 3/4 and 6/8 measure-boundary math.
- `musicxml/export.py`: assert the exported `TimeSignature`/`MetronomeMark`/`Key` reflect the score's stored values rather than fixed defaults.
- `score_schema`: `validate_score` accepts the v2 shape, rejects `schemaVersion: 1`, rejects a part missing `tempoBpm`/`meter`/`key`/`confidence`.

## Error Handling

No new `JobErrorCode` values. The one new failure path (`beat_track` finds no usable beats) reuses `MODEL_FAILED`. Low confidence on any of the three signals does **not** fail the job — it's stored as data for a future correction UI to surface, consistent with there being no such UI yet.

## Definition of Done

- A developer can point the pipeline at a fixture with a known BPM/meter/key and get back detected values within stated tolerances, with confidence scores stored on the canonical score.
- `quantize` and `musicxml/export.py` no longer contain any hardcoded tempo/meter/key constant.
- Full workspace test suite passes, including new coverage for `structure`, updated `quantize`/`musicxml` tests, and `score_schema` v2 validation.
