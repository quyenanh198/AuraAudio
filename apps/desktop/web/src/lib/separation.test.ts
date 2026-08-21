import { describe, expect, it } from "vitest";

import { separationAppliesToInstrument, separationNoopNote } from "./separation";

describe("separationAppliesToInstrument", () => {
  it("returns true for guitar", () => {
    expect(separationAppliesToInstrument("guitar")).toBe(true);
  });

  it("returns false for piano", () => {
    expect(separationAppliesToInstrument("piano")).toBe(false);
  });
});

describe("separationNoopNote", () => {
  it("returns null when unchecked, regardless of instrument", () => {
    expect(separationNoopNote(false, "guitar")).toBeNull();
    expect(separationNoopNote(false, "piano")).toBeNull();
  });

  it("returns null when checked and the instrument supports it (guitar)", () => {
    expect(separationNoopNote(true, "guitar")).toBeNull();
  });

  it("returns a non-empty note when checked and the instrument doesn't support it (piano)", () => {
    const note = separationNoopNote(true, "piano");
    expect(note).not.toBeNull();
    expect(note).toContain("Piano");
    expect(note).toContain("Guitar");
  });
});
