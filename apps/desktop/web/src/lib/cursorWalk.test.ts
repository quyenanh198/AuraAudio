import { describe, expect, it } from "vitest";

import { isRestOrAllTiedStep, isTieContinuation, type NoteLike } from "./cursorWalk";

function restNote(): NoteLike {
  return { isRest: () => true };
}

function plainNote(): NoteLike {
  return { isRest: () => false, NoteTie: null };
}

/** A tie-START note: has a NoteTie whose StartNote is itself. */
function tieStartNote(): NoteLike {
  const n: NoteLike = { isRest: () => false };
  n.NoteTie = { StartNote: n };
  return n;
}

/** A tie-CONTINUATION note: has a NoteTie whose StartNote is a DIFFERENT
 * note object (the real tie start, elsewhere in the score). */
function tieContinuationNote(): NoteLike {
  const otherStartNote = {};
  return { isRest: () => false, NoteTie: { StartNote: otherStartNote } };
}

describe("isTieContinuation", () => {
  it("returns false for a note with no tie at all", () => {
    expect(isTieContinuation(plainNote())).toBe(false);
  });

  it("returns false for a note that starts its own tie", () => {
    expect(isTieContinuation(tieStartNote())).toBe(false);
  });

  it("returns true for a note that is the continuation of a tie", () => {
    expect(isTieContinuation(tieContinuationNote())).toBe(true);
  });

  it("treats undefined NoteTie the same as null", () => {
    const n: NoteLike = { isRest: () => false };
    expect(isTieContinuation(n)).toBe(false);
  });
});

describe("isRestOrAllTiedStep", () => {
  it("is a rest step when there are no notes at all", () => {
    expect(isRestOrAllTiedStep([])).toBe(true);
  });

  it("is a rest step when every note is a rest", () => {
    expect(isRestOrAllTiedStep([restNote(), restNote()])).toBe(true);
  });

  it("is NOT a rest step for a plain, freshly-attacked note", () => {
    expect(isRestOrAllTiedStep([plainNote()])).toBe(false);
  });

  it("is NOT a rest step for a note that starts a tie (the first written note)", () => {
    expect(isRestOrAllTiedStep([tieStartNote()])).toBe(false);
  });

  // The core Bug D regression: a step whose only sounding note is a tie
  // CONTINUATION (the second, silently-inserted <note> the exporter wrote
  // for a duration that needed a tie) must be treated as a rest step, so
  // timeline.ts's buildTimeline() never sees it as an extra onset group
  // with no JSON counterpart.
  it("IS a rest step when the only sounding note is a tie continuation", () => {
    expect(isRestOrAllTiedStep([tieContinuationNote()])).toBe(true);
  });

  it("IS a rest step for a rest plus a tie continuation together", () => {
    expect(isRestOrAllTiedStep([restNote(), tieContinuationNote()])).toBe(true);
  });

  // A MIXED step — one voice/staff ties over while another starts a
  // genuinely new note at the same instant (e.g. a piano cross-hand chord,
  // or the guitar notation+TAB staves disagreeing because only one voice
  // happens to need a tie) — must still be recorded as a real step: the
  // new attack has no other step to be counted at.
  it("is NOT a rest step when a tie continuation is mixed with a real new attack", () => {
    expect(isRestOrAllTiedStep([tieContinuationNote(), plainNote()])).toBe(false);
  });

  it("is NOT a rest step when a tie continuation is mixed with a rest and a real attack", () => {
    expect(isRestOrAllTiedStep([tieContinuationNote(), restNote(), plainNote()])).toBe(false);
  });
});
