// Pure helpers for Task 7's Inspector + keyboard shortcuts: fraction
// arithmetic for onset/duration grid-stepping (mirrors timeline.ts's
// fraction handling — see that file's `parseFraction`/`normalizedOnsetKey`
// for the sibling implementation this one is deliberately kept in lockstep
// with, duplicated for the same reason correlate.ts duplicates it: small,
// behaviorally inert, and not worth threading a shared import through files
// scoped to different tasks), MIDI pitch <-> note-name formatting, and
// score-JSON event lookup. Nothing here touches the DOM, OSMD, or the
// `editor` store — callers (Sidebar.svelte, ScoreView.svelte) own turning
// these into `EditOp`s.

import type { ScoreEvent, ScoreJson } from "./types";

/** One grid step, as a fraction of a whole note — matches the brief's "the
 * grid step is a 16th = '1/16'" exactly. */
export const GRID_STEP = "1/16";

/**
 * Mirror of score_schema/meters.py::SUPPORTED_METERS — same values, same
 * order. Both sides pin this list with tests; change them together.
 */
export const METER_OPTIONS = [
  "2/4", "3/4", "4/4", "5/4", "2/2", "3/8", "6/8", "7/8", "9/8", "12/8",
] as const;

interface Fraction {
  n: number;
  d: number;
}

function gcd(a: number, b: number): number {
  let x = Math.abs(a);
  let y = Math.abs(b);
  while (y !== 0) {
    [x, y] = [y, x % y];
  }
  return x || 1;
}

function lcm(a: number, b: number): number {
  return Math.abs(a * b) / gcd(a, b);
}

function parseFraction(value: string): Fraction {
  const [numStr, denStr] = value.split("/");
  const n = Number(numStr);
  const d = Number(denStr);
  if (!Number.isFinite(n) || !Number.isFinite(d) || d === 0) {
    throw new Error(`invalid fraction: "${value}"`);
  }
  return { n, d };
}

function reduceFraction(f: Fraction): Fraction {
  let { n, d } = f;
  if (d < 0) {
    n = -n;
    d = -d;
  }
  const g = gcd(n, d);
  return { n: n / g, d: d / g };
}

function fractionToString(f: Fraction): string {
  const r = reduceFraction(f);
  return `${r.n}/${r.d}`;
}

function addFraction(a: Fraction, b: Fraction): Fraction {
  const d = lcm(a.d, b.d);
  return reduceFraction({ n: a.n * (d / a.d) + b.n * (d / b.d), d });
}

/** Sign of `a - b`, treating both as having positive denominators (true for
 * every fraction this module produces via `reduceFraction`). */
function compareFraction(a: Fraction, b: Fraction): number {
  return a.n * b.d - b.n * a.d;
}

function clampFraction(value: Fraction, min: Fraction, max: Fraction): Fraction {
  if (compareFraction(value, min) < 0) return min;
  if (compareFraction(value, max) > 0) return max;
  return value;
}

/** A measure's length in whole notes, from a meter string like "4/4" or
 * "3/4". Mirrors `score_schema.meters.beats_per_measure(meter) / 4` exactly
 * — `beats_per_measure("n/d") = n * 4/d`, so dividing by 4 leaves `n/d`
 * itself; i.e. the meter string, read as a fraction, already IS the
 * measure's length in whole notes. */
export function measureLengthWhole(meter: string): Fraction {
  return parseFraction(meter);
}

const ZERO: Fraction = { n: 0, d: 1 };
const GRID: Fraction = parseFraction(GRID_STEP);

/** `onset` (a whole-note fraction, e.g. "1/4") moved by one grid step
 * (`direction`: +1 forward, -1 back), clamped to `[0, measureLength -
 * GRID_STEP]` — the backend rejects `notatedOnset >= measureLength`
 * (score_schema/edits.py's `move_note` branch), so the largest step-aligned
 * value strictly inside the measure is `measureLength - 1/16`. */
export function stepOnset(onset: string, direction: 1 | -1, meter: string): string {
  const cur = parseFraction(onset);
  const step: Fraction = direction > 0 ? GRID : { n: -GRID.n, d: GRID.d };
  const next = addFraction(cur, step);
  const measureLen = measureLengthWhole(meter);
  const max = addFraction(measureLen, { n: -GRID.n, d: GRID.d });
  return fractionToString(clampFraction(next, ZERO, max));
}

/** `duration` (a whole-note fraction) moved by one grid step, clamped to a
 * minimum of one grid step (the backend rejects `notatedDuration <= 0`) —
 * no upper clamp, since the backend imposes none either. */
export function stepDuration(duration: string, direction: 1 | -1): string {
  const cur = parseFraction(duration);
  const step: Fraction = direction > 0 ? GRID : { n: -GRID.n, d: GRID.d };
  const next = addFraction(cur, step);
  if (compareFraction(next, GRID) < 0) return fractionToString(GRID);
  return fractionToString(next);
}

// --- Pitch <-> note name -----------------------------------------------

const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

export function clampPitch(pitch: number): number {
  return Math.min(127, Math.max(0, Math.round(pitch)));
}

/** Scientific pitch notation, e.g. 60 -> "C4" (middle C), 61 -> "C#4",
 * 0 -> "C-1", 127 -> "G9". Assumes `pitch` is already a valid MIDI number
 * (0-127) — callers that got `pitch` from a `ScoreEvent` or from
 * `clampPitch` already satisfy that. */
export function pitchToName(pitch: number): string {
  const name = NOTE_NAMES[((pitch % 12) + 12) % 12];
  const octave = Math.floor(pitch / 12) - 1;
  return `${name}${octave}`;
}

/** Inverse of the note-name half of `pitchToName`: a note name (one of
 * `NOTE_NAMES`) plus an octave -> MIDI pitch, clamped to 0-127. */
export function nameOctaveToPitch(name: string, octave: number): number {
  const idx = NOTE_NAMES.indexOf(name);
  const semitone = idx < 0 ? 0 : idx;
  return clampPitch((octave + 1) * 12 + semitone);
}

export const NOTE_NAME_OPTIONS: readonly string[] = NOTE_NAMES;

// --- Key display formatting ---------------------------------------------

const KEY_ACCIDENTAL_DISPLAY: Record<string, string> = { "#": "♯", "-": "♭" };

/** Bug 2 fix: score_schema's key format (`^[A-G](#|-)? (major|minor)$` --
 * see score_schema/validate.py and score_schema/edits.py's identical
 * `_KEY_PATTERN`) spells sharp/flat as plain ASCII "#"/"-" on the wire,
 * matching what music21/MusicXML tooling expects. That raw ASCII form
 * ("E- major", "F# minor") was leaking straight into the UI verbatim.
 *
 * DISPLAY-ONLY: this must never be applied to the value actually sent to
 * or stored by the backend (the key `<select>`'s own `value`, or any
 * `EditOp.value`) -- only what's shown to the user changes, e.g. in the
 * `<option>` text. Callers pass the RAW backend string through unchanged
 * everywhere else.
 *
 * Input that doesn't match the expected pattern (should never happen for
 * a value the backend already accepted/emitted, but a stray or future key
 * spelling shouldn't crash the Sidebar) is returned unchanged rather than
 * thrown on. */
export function formatKeyForDisplay(key: string): string {
  const match = /^([A-G])(#|-)?( (?:major|minor))$/.exec(key);
  if (!match) return key;
  const [, tonic, accidental, modeSuffix] = match;
  const displayAccidental = accidental ? KEY_ACCIDENTAL_DISPLAY[accidental] : "";
  return `${tonic}${displayAccidental}${modeSuffix}`;
}

// --- Add-note measure targeting -----------------------------------------

export type MeasureNumberValidation = { ok: true; measureNumber: number } | { ok: false; error: string };

/** Validates the Add-note form's measure-number field: `raw` must parse as
 * an integer within `[1, maxMeasure]` (score measures are numbered
 * contiguously 1..max — see score_schema's `_rebucket` invariant). Returns
 * either the parsed measure number or a user-facing error string, matching
 * the inline field-error pattern the rest of Sidebar's mini-forms already
 * use (tempo, fingering). */
export function validateMeasureNumber(raw: string, maxMeasure: number): MeasureNumberValidation {
  const trimmed = raw.trim();
  const value = Number(trimmed);
  if (trimmed === "" || !Number.isInteger(value)) {
    return { ok: false, error: `Measure must be a whole number between 1 and ${maxMeasure}.` };
  }
  if (value < 1 || value > maxMeasure) {
    return { ok: false, error: `Measure must be between 1 and ${maxMeasure}.` };
  }
  return { ok: true, measureNumber: value };
}

// --- Score-JSON event lookup --------------------------------------------

export interface FoundEvent {
  event: ScoreEvent;
  measureNumber: number;
}

/** Finds `eventId` across every measure of `score.parts[0]` (this codebase's
 * scores always have exactly one part — see timeline.ts/correlate.ts's same
 * assumption). Returns `null` for a missing/deleted event or a `null`
 * score/id, so callers (Inspector fields, keyboard shortcuts, the
 * refresh-loop's "was the selected note deleted?" check) can use it directly
 * as a existence check too. */
export function findEvent(score: ScoreJson | null, eventId: string | null): FoundEvent | null {
  if (!score || !eventId) return null;
  const part = score.parts[0];
  if (!part) return null;
  for (const measure of part.measures) {
    const event = measure.events.find((e) => e.id === eventId);
    if (event) return { event, measureNumber: measure.number };
  }
  return null;
}

/** The id of the first event in the first non-empty measure, in measure
 * order — used by the rederive-retry trick (any existing event's current
 * `locked` value re-applied via `set_locked` is a semantically-null edit
 * that still triggers a fresh rederive; see ScoreView.svelte's
 * `retryRederive`). `null` for an empty or missing score. */
export function firstEventId(score: ScoreJson | null): string | null {
  const part = score?.parts[0];
  if (!part) return null;
  for (const measure of part.measures) {
    if (measure.events.length > 0) return measure.events[0].id;
  }
  return null;
}
