# Beat, Meter, and Key Detection — Design

## Context

Phase 1 (the transcription vertical slice, merged to `main`) produces a canonical score by quantizing raw note events to a **hardcoded** 120 BPM / 4/4 grid — regardless of what the audio actually contains. `packages/musicxml/src/musicxml/export.py` compounds this: it hardcodes `TimeSignature("4/4")` and `MetronomeMark(number=120)` on every export, ignoring the score entirely. This sub-project (the first slice of ARCHITECTURE.md §10 Phase 2, item 1) replaces the hardcoded assumption with real per-clip detection of tempo, meter, and key, and threads the detected values through quantization and MusicXML export.

This is scoped as one sub-project out of five identified for Phase 2 (the others — guitar/piano assignment, the web client, PDF rendering, and the offline benchmark pipeline — are out of scope here and get their own specs).

## Goal

Given a short solo clip's raw note events and normalized audio, detect:
- a single global tempo (BPM) for the whole clip,
- the best-fit time signature from `{"4/4", "3/4"}`,
- the best-fit major/minor key,

each with a confidence score, and use those values (instead of hardcoded constants) to quantize notes and render MusicXML with a correct time signature, tempo marking, and key signature.

## Explicit Non-Goals (deferred to later sub-projects or phases)

- **Time-varying tempo.** One BPM per clip, not a tempo curve. Real recordings drift, but a global estimate is a large accuracy improvement over a hardcoded constant at a fraction of the implementation and testing cost; time-varying detection is a natural follow-up once this ships and its failure modes on real material are known.
- **Meters outside {4/4, 3/4}.** The design originally targeted {4/4, 3/4, 6/8, 2/4}; empirical prototyping against synthetic click fixtures (see Architecture) found that `librosa.beat.beat_track` locks onto whatever the *finest* audible pulse in the signal is rather than a stable perceptual tactus, which collapses the subdivision signal a simple/compound (6/8-vs-simple-meter) classifier needs — and that 2/4 is empirically indistinguishable from 4/4 by the same accent-periodicity technique that separates 4/4 from 3/4 cleanly (a 2/4 measure is just half of a 4/4 measure with the same accent pattern repeated). Both are dropped from v1. 4/4 and 3/4 alone still cover a large share of the target repertoire; 6/8/2/4 (and meters beyond) are a later, better-funded pass — likely via a note-event/velocity-accent-based technique using basic-pitch's own output instead of raw-audio onset analysis.
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
| Tempo + beat times | `librosa.beat.beat_track` | Already transitively installed (`basic-pitch` pulls `librosa` via its `resampy`/`audioread` chain) — zero new dependency weight. Well-tested, standard approach. Empirically validated against synthetic click fixtures at 120 BPM: detects ~117 BPM (a small, consistent estimator bias, not noise) — tolerance below is set from this measurement, not guessed. |
| Meter | Custom accent-periodicity scorer on `beat_track`'s own beat times + `librosa.onset.onset_strength` | Validated empirically (see below): sample onset-strength energy *at* each detected beat time (not between them), then test which grouping — every 3rd beat (3/4) or every 4th (4/4) — has one offset whose mean accent clearly exceeds the overall average. In the prototype this gave a clean, correctly-predicted margin for both candidates on synthetic fixtures. |
| Key | `music21`'s built-in key analysis (`Stream.analyze('key')`, Krumhansl-Schmuckler) | Already a direct dependency of `packages/musicxml`. Returns a `Key` object exposing `.correlationCoefficient`, usable directly as a confidence proxy. |

**Rejected:** `madmom` (more accurate joint beat/downbeat/meter model, but unmaintained with brittle old-numpy/Cython build pins — too much install risk for the accuracy gain at this stage) and a hand-rolled DSP tempo/beat estimator (reinvents a solved problem `librosa` already gives us for free, worse accuracy, no dependency savings since `librosa` is already in the tree via `basic-pitch`).

### Data flow

1. `structure.run(ctx, normalized_path, notes)`:
   - Load audio via `librosa.load` (already 22.05kHz mono from the `normalize` stage — no resampling needed).
   - `tempo, beat_frames = librosa.beat.beat_track(...)`; convert to `beat_times = librosa.frames_to_time(beat_frames, sr=sr)`.
   - Tempo confidence: `1 - clip(stddev(inter_beat_intervals) / mean(inter_beat_intervals), 0, 1)` — a simple, deterministic proxy for how metronomic the detected beat grid is (librosa doesn't expose a native confidence value).
   - Meter — the validated accent-periodicity scorer:
     1. `onset_env = librosa.onset.onset_strength(y=y, sr=sr)`, `onset_sr = sr / 512`.
     2. For each beat time `t`, sample `accent = max(onset_env[frames within ±50ms of t])` — a small window, not a single frame, so it isn't thrown off by frame-boundary rounding. This produces one accent value per beat.
     3. For each candidate group size `g` in `{3, 4}` (3/4, 4/4): compute `offset_scores = [mean(accents[offset::g]) for offset in range(g)]`, then `margin = max(offset_scores) - mean(accents)` — how much the best-aligned downbeat position stands out from the average.
     4. Pick the candidate with the larger margin; `meter_confidence = winning_margin / (margin_3 + margin_4)`, clipped to `[0, 1]` (falls back to `0.5` if both margins are `0`).
   - Key: build a `music21.stream.Stream` from the note pitches (rhythm-agnostic), call `.analyze('key')`, read `.tonic.name` / `.mode` / `.correlationCoefficient` (clipped to `[0, 1]`).
   - Cache via the existing `find_cached_artifact`/`save_artifact` pattern (stage `"structure"`, version 1) — same resume-on-retry behavior every other stage already has.
   - If `beat_track` returns fewer than 2 beats (near-silent or too-short clip — not enough to compute an inter-beat interval or score a meter candidate), raise `JobFailure(JobErrorCode.MODEL_FAILED, ...)` — reuses the existing error code, no new one needed.
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

- `meter` is one of `"4/4"`, `"3/4"`.
- `key` is `"<tonic> major"` or `"<tonic> minor"` (e.g. `"C major"`, `"A minor"`), matching `music21`'s naming so it round-trips directly into `music21.key.Key(...)`.
- Existing per-event fields (`notatedOnset`, `notatedDuration` as whole-note-fraction strings) are **unchanged** — this representation is already meter-agnostic, since it expresses position/duration as a fraction of a whole note rather than in beats. A fixed 1/16-whole-note grid applies uniformly across both candidate meters, so no meter-specific quantization branching is needed. Measure length in whole-note units becomes meter-dependent instead of always being `1` (4/4 → `1`, 3/4 → `3/4`), which is the only place meter enters the quantizer's math.

### Enharmonic spelling

Notes are spelled using `music21`'s key-aware pitch spelling once the part's `Key` is known — a pitch class like MIDI 66 spells as D♯ in a key where D is the more natural scale step and as E♭ where E-flat fits better, rather than Phase 1's arbitrary default. Exact `music21` API surface (`pitch.Pitch.getEnharmonic()` vs. building notes inside a keyed `Stream` and letting `makeNotation` resolve spelling) is an implementation-time decision, not fixed here.

## Testing

Phase 1's synthetic fixture (four notes 0.5s apart) has no strong periodic pulse — not enough signal for `beat_track` to lock onto reliably, and no tonal center for key analysis to find. Two new fixtures are needed in `packages/test_fixtures`:

- `write_metronome_pulse_wav(path, bpm, meter, duration_s)` — measured clicks (strong on beat 1, weaker elsewhere, one click per beat — **not** per subdivision, since injecting audible sub-beat clicks was what defeated the original 6/8 approach) at an exact known BPM/meter, so tempo can be asserted within an empirically-validated tolerance (±5 BPM — `beat_track` showed a consistent ~3 BPM low bias against a 120 BPM prototype fixture) and the meter scorer can be asserted to pick the correct candidate.
- `write_diatonic_melody_wav(path, key, duration_s)` — a short scale/arpeggio in a known key, so key analysis can be asserted against ground truth instead of merely "didn't crash."

Coverage needed:
- `structure.py`: known-tempo fixture → tempo within tolerance; known-meter fixture → correct candidate wins; known-key fixture → correct tonic/mode; a beats-not-found case → `JobFailure(MODEL_FAILED)`; a cache-resume test matching the existing stage pattern.
- `quantize.py`: existing tests updated to pass a `StructureResult` instead of relying on module constants; new tests for 3/4 measure-boundary math.
- `musicxml/export.py`: assert the exported `TimeSignature`/`MetronomeMark`/`Key` reflect the score's stored values rather than fixed defaults.
- `score_schema`: `validate_score` accepts the v2 shape, rejects `schemaVersion: 1`, rejects a part missing `tempoBpm`/`meter`/`key`/`confidence`.

## Error Handling

No new `JobErrorCode` values. The one new failure path (`beat_track` finds no usable beats) reuses `MODEL_FAILED`. Low confidence on any of the three signals does **not** fail the job — it's stored as data for a future correction UI to surface, consistent with there being no such UI yet.

## Definition of Done

- A developer can point the pipeline at a fixture with a known BPM/meter/key and get back detected values within stated tolerances, with confidence scores stored on the canonical score.
- `quantize` and `musicxml/export.py` no longer contain any hardcoded tempo/meter/key constant.
- Full workspace test suite passes, including new coverage for `structure`, updated `quantize`/`musicxml` tests, and `score_schema` v2 validation.
