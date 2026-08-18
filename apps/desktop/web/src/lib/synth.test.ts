import { describe, expect, it } from "vitest";

import { buildBuffers, schedulePlan, synthSamplerDefaults } from "./synth";
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

// Local, test-only note-name -> MIDI parser mirroring smplr's own
// `noteNameToMidi` convention (`#` for sharp) — deliberately NOT importing
// any of synth.ts's private helpers, so this test independently confirms
// buildBuffers()'s *output* is in the order that actually matters to
// smplr, rather than re-testing synth.ts's internals against themselves.
const SHARP_TO_SEMITONE: Record<string, number> = {
  C: 0,
  "C#": 1,
  D: 2,
  "D#": 3,
  E: 4,
  F: 5,
  "F#": 6,
  G: 7,
  "G#": 8,
  A: 9,
  "A#": 10,
  B: 11,
};
function smplrNameToMidi(name: string): number {
  const m = /^([A-G]#?)(-?\d+)$/.exec(name);
  if (!m) throw new Error(`not a smplr-parseable note name: "${name}"`);
  return SHARP_TO_SEMITONE[m[1]] + 12 * (Number(m[2]) + 1);
}

describe("buildBuffers", () => {
  // Regression test for a real, confirmed upstream bug in smplr@1.0.0's
  // Sampler({buffers}) path (see synth.ts's buildBuffers doc comment and
  // task-8-report.md's "Surprise" section): `samplerToPreset` computes
  // `spreadKeyRanges(midiEntries)` — which sorts by MIDI internally and
  // returns pitch/keyRange in *that* order — then zips the result
  // positionally against the *original, unsorted* `midiEntries` array. If
  // the buffers map's keys aren't already MIDI-ascending, every region
  // gets some OTHER entry's pitch (silently wrong at best, `NaN` and a
  // thrown `AudioParam` error at worst — confirmed live). buildBuffers()
  // must insert its output keys in MIDI-ascending order to work around
  // this, regardless of the input's (e.g. glob/filename-alphabetical)
  // order.
  it("inserts buffer keys in MIDI-ascending order, regardless of input (filename) order", () => {
    // Mimics import.meta.glob's real result shape/order: paths sorted
    // alphabetically by filename ("A2, A3, A4, A#2, ..."), which is NOT
    // MIDI-ascending (A#2's MIDI number is between A2 and A3's).
    const modules = {
      "/assets/soundfonts/guitar/A2.mp3": "url-A2",
      "/assets/soundfonts/guitar/A3.mp3": "url-A3",
      "/assets/soundfonts/guitar/A4.mp3": "url-A4",
      "/assets/soundfonts/guitar/As2.mp3": "url-As2",
      "/assets/soundfonts/guitar/As3.mp3": "url-As3",
      "/assets/soundfonts/guitar/B2.mp3": "url-B2",
      "/assets/soundfonts/guitar/C3.mp3": "url-C3",
      "/assets/soundfonts/guitar/G2.mp3": "url-G2", // lowest MIDI, but inserted last
    };

    const buffers = buildBuffers(modules);
    const keys = Object.keys(buffers);
    const midis = keys.map(smplrNameToMidi);

    expect(midis).toEqual([...midis].sort((a, b) => a - b));
    // G2 (lowest) must end up first despite being the last input entry.
    expect(keys[0]).toBe("G2");
  });

  it("keeps each note name mapped to its own URL after sorting (the sort must not shuffle name/url pairs apart)", () => {
    const modules = {
      "/x/A2.mp3": "url-A2",
      "/x/G2.mp3": "url-G2",
      "/x/C3.mp3": "url-C3",
    };

    const buffers = buildBuffers(modules);

    expect(buffers["G2"]).toBe("url-G2");
    expect(buffers["A2"]).toBe("url-A2");
    expect(buffers["C3"]).toBe("url-C3");
  });

  it("ignores files that don't match the tonejs note-name filename convention", () => {
    const modules = {
      "/x/A2.mp3": "url-A2",
      "/x/README.mp3": "url-not-a-note",
    };

    expect(buildBuffers(modules)).toEqual({ A2: "url-A2" });
  });
});

describe("synthSamplerDefaults", () => {
  // Regression test for the second half of the same smplr@1.0.0 bug (see
  // synth.ts's doc comment and task-8-report.md's "Surprise" section):
  // omitting decayTime/lpfCutoffHz/detune from the Sampler() call leaves
  // them `undefined`, which smplr's internal merge does not skip, poisoning
  // every note's computed detune to NaN and crashing Voice construction.
  // Pins the workaround itself, independent of any real AudioContext.
  it("returns concrete, finite numbers for decayTime/lpfCutoffHz/detune — never omitted or undefined", () => {
    const opts = synthSamplerDefaults();

    expect(opts.decayTime).not.toBeUndefined();
    expect(opts.lpfCutoffHz).not.toBeUndefined();
    expect(opts.detune).not.toBeUndefined();
    expect(Number.isFinite(opts.decayTime)).toBe(true);
    expect(Number.isFinite(opts.lpfCutoffHz)).toBe(true);
    expect(Number.isFinite(opts.detune)).toBe(true);
  });

  it("matches smplr's own PARAM_DEFAULTS values (0.3 / 20000 / 0) so behavior is unchanged from smplr's built-in fallback, not just non-undefined", () => {
    expect(synthSamplerDefaults()).toEqual({ decayTime: 0.3, lpfCutoffHz: 20000, detune: 0 });
  });
});
