import { describe, expect, it } from "vitest";

import { buildTimeline, cursorIndexAt, desiredNextCallsFor, planCursorMove } from "./timeline";
import type { ScoreEvent, ScoreJson } from "./types";

function ev(overrides: Partial<ScoreEvent> = {}): ScoreEvent {
  return {
    id: "n0",
    pitch: 60,
    onsetSeconds: 0,
    offsetSeconds: 1,
    notatedOnset: "0/1",
    notatedDuration: "1/4",
    voice: 1,
    confidence: 0.9,
    locked: false,
    ...overrides,
  };
}

function scoreWith(events: ScoreEvent[][]): ScoreJson {
  return {
    schemaVersion: 4,
    timeMap: [],
    parts: [
      {
        instrument: "guitar",
        tempoBpm: 120,
        meter: "4/4",
        key: "C major",
        confidence: { tempo: 1, meter: 1, key: 1 },
        measures: events.map((measureEvents, i) => ({ number: i + 1, events: measureEvents })),
      },
    ],
  };
}

describe("buildTimeline", () => {
  it("returns one entry per event, sorted ascending, when every event has a distinct onset", () => {
    const score = scoreWith([
      [
        ev({ id: "n0", onsetSeconds: 0.0, notatedOnset: "0/1" }),
        ev({ id: "n1", onsetSeconds: 0.5, notatedOnset: "1/8" }),
        ev({ id: "n2", onsetSeconds: 1.0, notatedOnset: "1/4" }),
      ],
    ]);

    const timeline = buildTimeline(score, [0, 1, 2]);

    expect(timeline).toEqual([
      { t: 0.0, step: 0 },
      { t: 0.5, step: 1 },
      { t: 1.0, step: 2 },
    ]);
  });

  it("collapses events sharing a notatedOnset (a chord) into one entry, keyed to one walked cursor step", () => {
    // Same notatedOnset (a real chord per packages/musicxml's grouping key)
    // but slightly different onsetSeconds (raw detection jitter) — must
    // still collapse to a single entry. This is the chord-rule
    // reconciliation: grouping is on notatedOnset, not onsetSeconds.
    const score = scoreWith([
      [
        ev({ id: "c0", onsetSeconds: 0.0, notatedOnset: "0/1" }),
        ev({ id: "c1", onsetSeconds: 0.012, notatedOnset: "0/1" }), // same chord, jittery onset
        ev({ id: "n2", onsetSeconds: 1.0, notatedOnset: "1/4" }),
      ],
    ]);

    // 2 distinct onsets -> 2 walked non-rest cursor steps.
    const timeline = buildTimeline(score, [0, 1]);

    expect(timeline).toEqual([
      { t: 0.0, step: 0 }, // min(0.0, 0.012)
      { t: 1.0, step: 1 },
    ]);
  });

  it("collapses same-notatedOnset events across hands (piano) the same way", () => {
    const score = scoreWith([
      [
        ev({ id: "left", onsetSeconds: 0.0, notatedOnset: "0/1", hand: "left" }),
        ev({ id: "right", onsetSeconds: 0.003, notatedOnset: "0/1", hand: "right" }),
      ],
    ]);

    const timeline = buildTimeline(score, [0]);

    expect(timeline).toEqual([{ t: 0.0, step: 0 }]);
  });

  it("does not collapse equal notatedOnset fractions written differently across measures", () => {
    // "0/1" in measure 1 and "0/1" in measure 2 are NOT the same instant —
    // notatedOnset is measure-relative (task-1b-report.md R2).
    const score = scoreWith([
      [ev({ id: "m1n0", onsetSeconds: 0.0, notatedOnset: "0/1" })],
      [ev({ id: "m2n0", onsetSeconds: 2.0, notatedOnset: "0/1" })],
    ]);

    const timeline = buildTimeline(score, [0, 1]);

    expect(timeline).toEqual([
      { t: 0.0, step: 0 },
      { t: 2.0, step: 1 },
    ]);
  });

  it("treats equal notatedOnset fractions in different forms (1/2 vs 2/4) as the same onset", () => {
    const score = scoreWith([
      [
        ev({ id: "a", onsetSeconds: 1.0, notatedOnset: "1/2" }),
        ev({ id: "b", onsetSeconds: 1.0, notatedOnset: "2/4" }),
      ],
    ]);

    const timeline = buildTimeline(score, [0]);

    expect(timeline).toEqual([{ t: 1.0, step: 0 }]);
  });

  it("returns [] for an empty score", () => {
    const score = scoreWith([]);
    expect(buildTimeline(score, [])).toEqual([]);
  });

  it("returns [] for a score whose only measure has no events", () => {
    const score = scoreWith([[]]);
    expect(buildTimeline(score, [])).toEqual([]);
  });

  it("sorts by onset even when the input array is not time-ordered (real transcription data isn't)", () => {
    // Verified against a real project's GET /v1/projects/{id}/score
    // response: basic-pitch's note order (which quantize.py enumerates
    // as-is, with no sort) is not onset-ordered. Shape mirrors that real
    // case — later-in-array events have earlier onsets.
    const score = scoreWith([
      [
        ev({ id: "n0", onsetSeconds: 1.0, notatedOnset: "1/4" }),
        ev({ id: "n1", onsetSeconds: 0.5, notatedOnset: "1/8" }),
        ev({ id: "n2", onsetSeconds: 0.0, notatedOnset: "0/1" }),
      ],
    ]);

    const timeline = buildTimeline(score, [0, 1, 2]);

    expect(timeline).toEqual([
      { t: 0.0, step: 0 },
      { t: 0.5, step: 1 },
      { t: 1.0, step: 2 },
    ]);
  });

  it("groups a chord correctly even when its members are far apart in the array (real data shape)", () => {
    // Mirrors the real guitar project exactly: note_00 (t≈1.50) ...
    // note_05 (t≈0.03, notatedOnset "0/1") ... note_07 (t≈0.01, notatedOnset
    // "0/1", 6 slots away from its chord partner).
    const score = scoreWith([
      [
        ev({ id: "note_00", onsetSeconds: 1.5, notatedOnset: "3/4" }),
        ev({ id: "note_01", onsetSeconds: 1.0, notatedOnset: "1/2" }),
        ev({ id: "note_02", onsetSeconds: 0.99, notatedOnset: "1/2" }),
        ev({ id: "note_03", onsetSeconds: 0.52, notatedOnset: "1/4" }),
        ev({ id: "note_04", onsetSeconds: 0.49, notatedOnset: "1/4" }),
        ev({ id: "note_05", onsetSeconds: 0.03, notatedOnset: "0/1" }),
        ev({ id: "note_06", onsetSeconds: 1.46, notatedOnset: "11/16" }),
        ev({ id: "note_07", onsetSeconds: 0.01, notatedOnset: "0/1" }),
      ],
    ]);

    // 8 events, 4 real chord pairs -> 5 distinct onset groups.
    const timeline = buildTimeline(score, [0, 1, 2, 3, 4]);

    expect(timeline.map((entry) => entry.t)).toEqual([0.01, 0.49, 0.99, 1.46, 1.5]);
    expect(timeline.map((entry) => entry.step)).toEqual([0, 1, 2, 3, 4]);
  });

  it("contributes no groups for a silent (empty-events) measure between two real measures", () => {
    // quantize.py's silent-measure fidelity fix now emits a fully-silent
    // interior measure as {"number": n, "events": []} instead of omitting
    // it. buildTimeline must add zero onset groups for it — the walked
    // OSMD cursor step for its whole-measure rest is filtered out
    // upstream (ScoreView.svelte's walkNonRestStepIndices), so the two
    // counts must still agree at 2, not 3.
    const score = scoreWith([
      [ev({ id: "m1n0", onsetSeconds: 0.0, notatedOnset: "0/1" })],
      [], // silent measure — contributes nothing
      [ev({ id: "m3n0", onsetSeconds: 2.0, notatedOnset: "0/1" })],
    ]);

    const timeline = buildTimeline(score, [0, 1]);

    expect(timeline).toEqual([
      { t: 0.0, step: 0 },
      { t: 2.0, step: 1 },
    ]);
  });

  it("throws when nonRestStepIndices has fewer entries than distinct onsets", () => {
    const score = scoreWith([[ev({ id: "n0" }), ev({ id: "n1", onsetSeconds: 1, notatedOnset: "1/4" })]]);
    expect(() => buildTimeline(score, [0])).toThrow(/mismatch|non-rest cursor step/);
  });

  it("throws when nonRestStepIndices has more entries than distinct onsets", () => {
    const score = scoreWith([[ev({ id: "n0" })]]);
    expect(() => buildTimeline(score, [0, 1])).toThrow(/mismatch/);
  });
});

describe("cursorIndexAt", () => {
  const timeline = [
    { t: 0.0, step: 0 },
    { t: 1.0, step: 1 },
    { t: 2.5, step: 3 },
  ];

  it("returns -1 before the first entry", () => {
    expect(cursorIndexAt(timeline, -1)).toBe(-1);
  });

  it("returns -1 for an empty timeline", () => {
    expect(cursorIndexAt([], 5)).toBe(-1);
  });

  it("returns the exact index on an exact hit", () => {
    expect(cursorIndexAt(timeline, 1.0)).toBe(1);
  });

  it("returns the last entry at or before t, between entries", () => {
    expect(cursorIndexAt(timeline, 1.7)).toBe(1);
  });

  it("returns the last index after the last entry", () => {
    expect(cursorIndexAt(timeline, 100)).toBe(2);
  });
});

describe("desiredNextCallsFor", () => {
  const timeline = [
    { t: 0.0, step: 0 },
    { t: 1.0, step: 2 }, // a rest step (index 1) was skipped
    { t: 2.5, step: 5 },
  ];

  it("is 0 for timelineIndex -1 (nothing started yet)", () => {
    expect(desiredNextCallsFor(timeline, -1)).toBe(0);
  });

  it("is the entry's real OSMD step otherwise", () => {
    expect(desiredNextCallsFor(timeline, 0)).toBe(0);
    expect(desiredNextCallsFor(timeline, 1)).toBe(2);
    expect(desiredNextCallsFor(timeline, 2)).toBe(5);
  });
});

describe("planCursorMove", () => {
  it("is a no-op when already at the desired position", () => {
    expect(planCursorMove(3, 3)).toEqual({ reset: false, nextCalls: 0 });
  });

  it("moves forward with next() calls only", () => {
    expect(planCursorMove(2, 5)).toEqual({ reset: false, nextCalls: 3 });
  });

  it("moves backward via reset() + next() calls from step 0", () => {
    expect(planCursorMove(5, 2)).toEqual({ reset: true, nextCalls: 2 });
  });

  it("moving backward to step 0 is reset() with zero next() calls", () => {
    expect(planCursorMove(4, 0)).toEqual({ reset: true, nextCalls: 0 });
  });
});
