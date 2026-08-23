import { describe, expect, it } from "vitest";

import {
  METER_OPTIONS,
  clampPitch,
  findEvent,
  firstEventId,
  formatKeyForDisplay,
  measureLengthWhole,
  nameOctaveToPitch,
  pitchToName,
  stepDuration,
  stepOnset,
  validateMeasureNumber,
} from "./noteEdit";
import type { ScoreEvent, ScoreJson } from "./types";

function event(overrides: Partial<ScoreEvent> = {}): ScoreEvent {
  return {
    id: "e1",
    pitch: 60,
    onsetSeconds: 0,
    offsetSeconds: 0.5,
    notatedOnset: "0/1",
    notatedDuration: "1/4",
    voice: 1,
    confidence: 0.9,
    locked: false,
    ...overrides,
  };
}

function score(events: ScoreEvent[]): ScoreJson {
  return {
    schemaVersion: 4,
    timeMap: [
      { beat: 0, seconds: 0 },
      { beat: 1, seconds: 0.5 },
    ],
    parts: [
      {
        instrument: "guitar",
        tempoBpm: 120,
        meter: "4/4",
        key: "C major",
        confidence: { tempo: 1, meter: 1, key: 1 },
        measures: [{ number: 1, events }],
      },
    ],
  };
}

describe("measureLengthWhole", () => {
  it("reads meter strings as whole-note fractions directly", () => {
    expect(measureLengthWhole("4/4")).toEqual({ n: 4, d: 4 });
    expect(measureLengthWhole("3/4")).toEqual({ n: 3, d: 4 });
  });
});

describe("stepOnset", () => {
  it("moves forward and back by one grid step (1/16)", () => {
    expect(stepOnset("1/4", 1, "4/4")).toBe("5/16");
    expect(stepOnset("1/4", -1, "4/4")).toBe("3/16");
  });

  it("clamps at 0 when stepping back from the start of the measure", () => {
    expect(stepOnset("0/1", -1, "4/4")).toBe("0/1");
    expect(stepOnset("1/16", -1, "4/4")).toBe("0/1");
  });

  it("clamps at measureLength - 1/16 when stepping past the end (4/4)", () => {
    expect(stepOnset("15/16", 1, "4/4")).toBe("15/16");
    expect(stepOnset("14/16", 1, "4/4")).toBe("15/16");
  });

  it("clamps at measureLength - 1/16 for a 3/4 measure", () => {
    // 3/4 meter -> measure length 3/4 whole notes = 12/16; last valid tick 11/16.
    expect(stepOnset("11/16", 1, "3/4")).toBe("11/16");
    expect(stepOnset("10/16", 1, "3/4")).toBe("11/16");
  });

  it("normalizes non-16ths-denominator input onto the grid", () => {
    expect(stepOnset("1/2", 1, "4/4")).toBe("9/16");
  });
});

describe("stepDuration", () => {
  it("moves forward and back by one grid step", () => {
    expect(stepDuration("1/4", 1)).toBe("5/16");
    expect(stepDuration("1/4", -1)).toBe("3/16");
  });

  it("clamps at a minimum of one grid step (never reaches/crosses zero)", () => {
    expect(stepDuration("1/16", -1)).toBe("1/16");
  });

  it("has no upper clamp", () => {
    expect(stepDuration("1/1", 1)).toBe("17/16");
  });
});

describe("pitch <-> name", () => {
  it("formats known MIDI numbers in scientific pitch notation", () => {
    expect(pitchToName(60)).toBe("C4");
    expect(pitchToName(61)).toBe("C#4");
    expect(pitchToName(59)).toBe("B3");
    expect(pitchToName(0)).toBe("C-1");
    expect(pitchToName(127)).toBe("G9");
  });

  it("clampPitch clamps to the valid MIDI range and rounds", () => {
    expect(clampPitch(-5)).toBe(0);
    expect(clampPitch(200)).toBe(127);
    expect(clampPitch(60.6)).toBe(61);
  });

  it("nameOctaveToPitch round-trips through pitchToName", () => {
    expect(nameOctaveToPitch("C", 4)).toBe(60);
    expect(nameOctaveToPitch("C#", 4)).toBe(61);
    expect(nameOctaveToPitch("C", -1)).toBe(0);
    expect(nameOctaveToPitch("G", 9)).toBe(127);
  });
});

describe("findEvent", () => {
  it("finds an event and its measure number", () => {
    const s = score([event({ id: "e1" }), event({ id: "e2" })]);
    const found = findEvent(s, "e2");
    expect(found?.event.id).toBe("e2");
    expect(found?.measureNumber).toBe(1);
  });

  it("returns null for a missing event, null score, or null id", () => {
    const s = score([event({ id: "e1" })]);
    expect(findEvent(s, "does-not-exist")).toBeNull();
    expect(findEvent(null, "e1")).toBeNull();
    expect(findEvent(s, null)).toBeNull();
  });
});

describe("validateMeasureNumber", () => {
  it("accepts an in-range integer", () => {
    expect(validateMeasureNumber("3", 8)).toEqual({ ok: true, measureNumber: 3 });
    expect(validateMeasureNumber("1", 8)).toEqual({ ok: true, measureNumber: 1 });
    expect(validateMeasureNumber("8", 8)).toEqual({ ok: true, measureNumber: 8 });
  });

  it("rejects a measure number below 1 or above the score's max measure", () => {
    expect(validateMeasureNumber("0", 8)).toEqual({
      ok: false,
      error: "Measure must be between 1 and 8.",
    });
    expect(validateMeasureNumber("9", 8)).toEqual({
      ok: false,
      error: "Measure must be between 1 and 8.",
    });
  });

  it("rejects a non-integer value", () => {
    expect(validateMeasureNumber("2.5", 8)).toEqual({
      ok: false,
      error: "Measure must be a whole number between 1 and 8.",
    });
    expect(validateMeasureNumber("abc", 8)).toEqual({
      ok: false,
      error: "Measure must be a whole number between 1 and 8.",
    });
    expect(validateMeasureNumber("", 8)).toEqual({
      ok: false,
      error: "Measure must be a whole number between 1 and 8.",
    });
  });
});

describe("firstEventId", () => {
  it("returns the first event of the first non-empty measure", () => {
    const s: ScoreJson = score([]);
    s.parts[0].measures = [
      { number: 1, events: [] },
      { number: 2, events: [event({ id: "e5" }), event({ id: "e6" })] },
    ];
    expect(firstEventId(s)).toBe("e5");
  });

  it("returns null for an empty score", () => {
    expect(firstEventId(score([]))).toBeNull();
    expect(firstEventId(null)).toBeNull();
  });
});

describe("METER_OPTIONS", () => {
  it("mirrors score_schema.meters.SUPPORTED_METERS exactly", () => {
    expect(METER_OPTIONS).toEqual([
      "2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8",
    ]);
  });
});

describe("stepOnset with 6/8 and 7/8", () => {
  it("wraps within a 6/8 measure boundary (measureLength 3/4 = 12/16)", () => {
    // 6/8 measure = 6/8 = 3/4 whole notes; last valid onset is 11/16
    const atMax = stepOnset("11/16", 1, "6/8");
    expect(atMax).toBe("11/16"); // clamps at max
  });

  it("clamps at 7/8 measure end (measureLength 7/8 = 14/16, max onset 13/16)", () => {
    // 7/8 measure = 7/8 whole notes; last valid onset is 13/16
    const atMax = stepOnset("13/16", 1, "7/8");
    expect(atMax).toBe("13/16"); // clamps at max
  });

  it("clamps at 0 for 6/8 when stepping back from start", () => {
    const stepped = stepOnset("0/1", -1, "6/8");
    expect(stepped).toBe("0/1");
  });
});

describe("formatKeyForDisplay", () => {
  const LETTERS = ["A", "B", "C", "D", "E", "F", "G"] as const;
  const ACCIDENTALS = [
    { raw: "", display: "" },
    { raw: "#", display: "♯" },
    { raw: "-", display: "♭" },
  ] as const;
  const MODES = ["major", "minor"] as const;

  // Bug 2 fix: all 7 letters x sharp/flat/natural x major/minor -- the
  // full matrix score_schema's `^[A-G](#|-)? (major|minor)$` pattern
  // allows (this is a pure string transform, not a musical-realism check,
  // so it covers every syntactically valid key the backend could emit,
  // not just the ones KEY_TONICS in Sidebar.svelte happens to offer).
  for (const letter of LETTERS) {
    for (const accidental of ACCIDENTALS) {
      for (const mode of MODES) {
        const raw = `${letter}${accidental.raw} ${mode}`;
        const expected = `${letter}${accidental.display} ${mode}`;
        it(`"${raw}" -> "${expected}"`, () => {
          expect(formatKeyForDisplay(raw)).toBe(expected);
        });
      }
    }
  }

  it("never mutates a natural key's spelling (no accidental to map)", () => {
    expect(formatKeyForDisplay("C major")).toBe("C major");
    expect(formatKeyForDisplay("A minor")).toBe("A minor");
  });

  it("maps '-' to a real flat sign, not a hyphen-minus lookalike", () => {
    expect(formatKeyForDisplay("E- major")).toBe("E♭ major");
    expect(formatKeyForDisplay("E- major")).not.toContain("-");
  });

  it("maps '#' to a real sharp sign, not the ASCII number-sign", () => {
    expect(formatKeyForDisplay("F# minor")).toBe("F♯ minor");
    expect(formatKeyForDisplay("F# minor")).not.toContain("#");
  });

  it("returns unrecognized input unchanged rather than throwing", () => {
    expect(formatKeyForDisplay("")).toBe("");
    expect(formatKeyForDisplay("not a key")).toBe("not a key");
    expect(formatKeyForDisplay("H major")).toBe("H major");
    expect(formatKeyForDisplay("C phrygian")).toBe("C phrygian");
  });
});
