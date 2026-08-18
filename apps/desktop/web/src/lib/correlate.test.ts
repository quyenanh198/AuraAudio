import { describe, expect, it } from "vitest";

import { buildEventPositionIndex, nearestEvent, type StepNoteInfo } from "./correlate";
import type { TimelineEntry } from "./timeline";
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

describe("buildEventPositionIndex", () => {
  it("dedupes a two-staff guitar duplicate (same pitch, two staffIds) down to one event position", () => {
    const score = scoreWith([[ev({ id: "n0", pitch: 64, notatedOnset: "0/1", onsetSeconds: 0 })]]);
    const timeline: TimelineEntry[] = [{ t: 0, step: 0 }];
    const walk: StepNoteInfo[] = [
      {
        step: 0,
        notes: [
          { pitch: 64, staffId: 0, x: 10, y: 20 },
          { pitch: 64, staffId: 1, x: 10, y: 40 },
        ],
      },
    ];

    const index = buildEventPositionIndex(walk, timeline, score);

    expect(index).toHaveLength(1);
    expect(index[0]).toEqual({ eventId: "n0", x: 10, y: 20, pitch: 64 });
  });

  it("maps chord pitches (two distinct pitches at one step) to distinct eventIds", () => {
    const score = scoreWith([
      [
        ev({ id: "low", pitch: 60, notatedOnset: "0/1", onsetSeconds: 0 }),
        ev({ id: "high", pitch: 64, notatedOnset: "0/1", onsetSeconds: 0.01 }),
      ],
    ]);
    const timeline: TimelineEntry[] = [{ t: 0, step: 0 }];
    const walk: StepNoteInfo[] = [
      {
        step: 0,
        notes: [
          { pitch: 60, staffId: 0, x: 1, y: 1 },
          { pitch: 64, staffId: 0, x: 1, y: 5 },
        ],
      },
    ];

    const index = buildEventPositionIndex(walk, timeline, score);

    expect(index).toHaveLength(2);
    const byId = new Map(index.map((p) => [p.eventId, p]));
    expect(byId.get("low")).toEqual({ eventId: "low", x: 1, y: 1, pitch: 60 });
    expect(byId.get("high")).toEqual({ eventId: "high", x: 1, y: 5, pitch: 64 });
  });

  it("resolves equal-pitch chord members (same staff unison) by document order", () => {
    const score = scoreWith([
      [
        ev({ id: "voice1", pitch: 60, notatedOnset: "0/1", onsetSeconds: 0 }),
        ev({ id: "voice2", pitch: 60, notatedOnset: "0/1", onsetSeconds: 0 }),
      ],
    ]);
    const timeline: TimelineEntry[] = [{ t: 0, step: 0 }];
    const walk: StepNoteInfo[] = [
      {
        step: 0,
        notes: [
          { pitch: 60, staffId: 0, x: 5, y: 5 },
          { pitch: 60, staffId: 0, x: 5, y: 9 },
        ],
      },
    ];

    const index = buildEventPositionIndex(walk, timeline, score);

    expect(index).toEqual([
      { eventId: "voice1", x: 5, y: 5, pitch: 60 },
      { eventId: "voice2", x: 5, y: 9, pitch: 60 },
    ]);
  });

  it("throws when the score's onset grouping and the timeline disagree in length", () => {
    const score = scoreWith([[ev({ id: "n0" }), ev({ id: "n1", notatedOnset: "1/4", onsetSeconds: 1 })]]);
    const timeline: TimelineEntry[] = [{ t: 0, step: 0 }];
    const walk: StepNoteInfo[] = [{ step: 0, notes: [{ pitch: 60, staffId: 0, x: 0, y: 0 }] }];

    expect(() => buildEventPositionIndex(walk, timeline, score)).toThrow(/onset group/);
  });

  it("throws when a timeline step has no matching walked note info", () => {
    const score = scoreWith([[ev({ id: "n0" })]]);
    const timeline: TimelineEntry[] = [{ t: 0, step: 3 }];
    const walk: StepNoteInfo[] = [{ step: 0, notes: [{ pitch: 60, staffId: 0, x: 0, y: 0 }] }];

    expect(() => buildEventPositionIndex(walk, timeline, score)).toThrow(/no walked note info/);
  });
});

describe("nearestEvent", () => {
  const index = [
    { eventId: "a", x: 0, y: 0, pitch: 60 },
    { eventId: "b", x: 100, y: 0, pitch: 64 },
    { eventId: "c", x: 100, y: 30, pitch: 67 },
  ];

  it("picks the closest event within the distance threshold", () => {
    expect(nearestEvent(index, 98, 4, 20)).toBe("b");
  });

  it("returns null when nothing is within the threshold", () => {
    expect(nearestEvent(index, 500, 500, 20)).toBeNull();
  });

  it("returns null for an empty index", () => {
    expect(nearestEvent([], 0, 0, 100)).toBeNull();
  });
});
