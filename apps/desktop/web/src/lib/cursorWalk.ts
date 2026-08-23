// Pure tie-awareness logic for ScoreView.svelte's `walkCursor` — extracted
// so it's unit-testable without a real, mounted OSMD instance, mirroring
// timeline.ts/correlate.ts's own "pure math, no OSMD/DOM" split (see those
// files' module docstrings). Only structurally typed against the tiny slice
// of OSMD's real `Note`/`Tie` shape this needs (`isRest()`, `.NoteTie`,
// `.StartNote`) — a real `opensheetmusicdisplay` `Note` satisfies this
// interface, so ScoreView.svelte can pass its real `Note[]` straight
// through with no adapter/wrapping needed.
//
// Bug D root cause (see this project's incident notes / the Vitest suite
// below for the full trail): `quantize.py`'s 16th-note quantization grid
// routinely produces notated durations that aren't representable as a
// single note value (e.g. "5/16" = 1.25 quarterLength, a quarter tied to a
// sixteenth) — `musicxml.export`'s music21 writer silently splits these
// into MULTIPLE tied `<note>` elements for what is, in the score JSON,
// exactly ONE event with ONE `notatedOnset` (ties are not modeled in the
// score schema at all). `timeline.ts`'s `buildTimeline` groups JSON events
// one-group-per-onset, while OSMD's real cursor — walking the rendered
// MusicXML — DOES stop at a tied continuation note's own onset (it
// genuinely occupies its own position/duration in the graphical score,
// immediately following the tie-start note). Left unfiltered, every tie
// produces one extra non-rest cursor step with no corresponding JSON onset
// group, tripping `buildTimeline`'s own count-mismatch guard and surfacing
// as ScoreView's "Playback sync unavailable" banner — reliably, on real
// dense transcriptions whose quantized durations routinely land on
// non-power-of-two sixteenth counts, but never on this project's synthetic
// e2e fixtures (clean quarter/eighth/sixteenth durations that never need a
// tie).

/** The minimal slice of OSMD's real `Tie` class this module needs. */
export interface TieLike {
  StartNote: unknown;
}

/** The minimal slice of OSMD's real `Note` class this module needs — a real
 * `opensheetmusicdisplay` `Note` satisfies this structurally. */
export interface NoteLike {
  isRest(): boolean;
  NoteTie?: TieLike | null;
}

/** Whether `note` is the CONTINUATION half of a tie (a "stop"/middle tied
 * note), not a new attack — i.e. it has a `NoteTie` and is NOT that tie's
 * `StartNote`. A note with no tie at all (`NoteTie` null/undefined) is
 * never a continuation. */
export function isTieContinuation(note: NoteLike): boolean {
  const tie = note.NoteTie;
  return tie != null && tie.StartNote !== note;
}

/** Whether an OSMD cursor step (its full `notesUnderCursor()` list) should
 * be treated as a REST step for playback-sync purposes: either genuinely
 * empty/all-rests, or — the fix this module exists for — every remaining
 * (non-rest) note is a tie continuation, meaning the step is real (still
 * occupies a cursor position, still gets `next()`-ed) but is NOT a new
 * musical attack that any JSON score event's `notatedOnset` corresponds to.
 *
 * A MIXED step (e.g. one voice ties over while another voice/staff starts a
 * genuinely new note at the same instant) is deliberately NOT treated as a
 * rest step here — the new attack still needs to be recorded. */
export function isRestOrAllTiedStep(notes: NoteLike[]): boolean {
  const sounding = notes.filter((note) => !note.isRest());
  return sounding.length === 0 || sounding.every(isTieContinuation);
}
