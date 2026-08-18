import { describe, expect, it } from "vitest";

import { schedulePlan } from "./synth";
import type { ScoreEvent } from "./types";

function event(overrides: Partial<ScoreEvent> = {}): ScoreEvent {
  return {
    id: "note",
    pitch: 60,
    onsetSeconds: 0,
    offsetSeconds: 1,
    notatedOnset: "0/1",
    notatedDuration: "1/4",
    voice: 1,
    confidence: 1,
    locked: false,
    ...overrides,
  };
}

describe("schedulePlan", () => {
  it("returns an empty plan for an empty event list", () => {
    expect(schedulePlan([], 0)).toEqual([]);
  });

  it("schedules every event from the start when from=0", () => {
    const events = [
      event({ id: "a", pitch: 60, onsetSeconds: 0, offsetSeconds: 0.5 }),
      event({ id: "b", pitch: 64, onsetSeconds: 0.5, offsetSeconds: 1.5 }),
    ];

    expect(schedulePlan(events, 0)).toEqual([
      { at: 0, dur: 0.5, pitch: 60 },
      { at: 0.5, dur: 1, pitch: 64 },
    ]);
  });

  it("drops events entirely before `from` and offsets the rest relative to it", () => {
    const events = [
      event({ id: "a", pitch: 60, onsetSeconds: 0, offsetSeconds: 0.5 }),
      event({ id: "b", pitch: 64, onsetSeconds: 1, offsetSeconds: 1.5 }),
      event({ id: "c", pitch: 67, onsetSeconds: 2, offsetSeconds: 2.5 }),
    ];

    // Skipping mid-piece: `from` lands exactly on event b's onset.
    expect(schedulePlan(events, 1)).toEqual([
      { at: 0, dur: 0.5, pitch: 64 },
      { at: 1, dur: 0.5, pitch: 67 },
    ]);
  });

  it("includes an event whose onset exactly equals `from` (>=, not >)", () => {
    const events = [event({ onsetSeconds: 2, offsetSeconds: 2.5 })];

    expect(schedulePlan(events, 2)).toEqual([{ at: 0, dur: 0.5, pitch: 60 }]);
  });

  it("keeps chord events (same onset, different pitch) as separate simultaneous entries", () => {
    const events = [
      event({ id: "a", pitch: 60, onsetSeconds: 1, offsetSeconds: 2 }),
      event({ id: "b", pitch: 64, onsetSeconds: 1, offsetSeconds: 2 }),
      event({ id: "c", pitch: 67, onsetSeconds: 1, offsetSeconds: 2 }),
    ];

    const plan = schedulePlan(events, 0);
    expect(plan).toHaveLength(3);
    expect(plan.every((n) => n.at === 1 && n.dur === 1)).toBe(true);
    expect(plan.map((n) => n.pitch).sort((a, b) => a - b)).toEqual([60, 64, 67]);
  });

  it("sorts the output by onset even when the input array is not sorted", () => {
    // Task 7 finding (timeline.ts): score JSON events are not guaranteed to
    // be in ascending-onset order.
    const events = [
      event({ id: "c", pitch: 67, onsetSeconds: 2, offsetSeconds: 2.5 }),
      event({ id: "a", pitch: 60, onsetSeconds: 0, offsetSeconds: 0.5 }),
      event({ id: "b", pitch: 64, onsetSeconds: 1, offsetSeconds: 1.5 }),
    ];

    expect(schedulePlan(events, 0).map((n) => n.at)).toEqual([0, 1, 2]);
  });

  it("clamps a negative duration (offset before onset, malformed data) to 0 rather than throwing", () => {
    const events = [event({ onsetSeconds: 1, offsetSeconds: 0.5 })];

    expect(schedulePlan(events, 0)).toEqual([{ at: 1, dur: 0, pitch: 60 }]);
  });
});
