// Pure sync math between the OSMD graphical cursor walk (raw, unit-agnostic
// x/y — see Notation.svelte for the OSMD-unit-to-CSS-px conversion, which
// deliberately does NOT live here) and the score-JSON events those graphical
// notes correspond to. Nothing in this file touches OSMD, the DOM, or CSS
// pixels — the actual cursor walk that produces `StepNoteInfo[]` lives in
// ScoreView.svelte (it needs a live OSMDCursorHandle; see its
// `walkCursor`), extending the SAME walk that already builds
// `nonRestStepIndices` for timeline.ts's `buildTimeline` — this file only
// consumes both outputs. See task-6-report.md "correlate.ts design" for the
// full rationale.

import type { ScoreEvent, ScoreJson } from "./types";
import type { TimelineEntry } from "./timeline";

/** What ScoreView's one-time (well: once per load, and once per OSMD
 * re-render — see Notation.svelte's `onRerender`) cursor walk records for a
 * single non-rest cursor step: every non-rest note under the cursor at that
 * step, zipped from `OSMDCursorHandle.notesUnderCursor()[i]` (pitch/staff)
 * and `.gNotesUnderCursor()[i]` (graphical x/y) — verified index-aligned
 * against the installed 2.1.2 bundle: `Cursor.NotesUnderCursor` and
 * `.GNotesUnderCursor` both `forEach` the exact same
 * `VoicesUnderCursor().Notes` array, one via a bare push and the other via
 * `.map(rules.GNote)`, so index i is the same underlying note in both.
 * `pitch` is real MIDI (e.g. middle C = 60) — computed from the `Note`'s
 * `Pitch` (fundamental + octave + accidental) by the walker, NOT here (see
 * ScoreView.svelte's `midiPitchOf` for the formula and why it reads
 * `Pitch`'s parts rather than OSMD's own `Note.halfTone`), and NOT CSS
 * pixels: `x`/`y` are `GraphicalNote.PositionAndShape.AbsolutePosition`
 * verbatim, in raw OSMD engraving units. */
export interface StepNoteInfo {
  /** The real OSMD cursor step index this entry was walked at (matches
   * `TimelineEntry.step`). */
  step: number;
  notes: Array<{
    /** Real MIDI note number, matching `ScoreEvent.pitch`. */
    pitch: number;
    /** `Note.ParentStaff.Id` — distinguishes e.g. a guitar score's notation
     * staff from its TAB staff (same note, same pitch, two staves). */
    staffId: number;
    /** Raw OSMD engraving units — NOT CSS pixels. */
    x: number;
    /** Raw OSMD engraving units — NOT CSS pixels. */
    y: number;
  }>;
}

/** One score event correlated to where it is drawn. `x`/`y` are inherited
 * verbatim from the `StepNoteInfo` note this was matched to — still raw
 * OSMD units, unit-agnostic (Notation.svelte converts to CSS px on the way
 * out of `getEventPositions()`/`highlightEvent()`). */
export interface EventPosition {
  eventId: string;
  x: number;
  y: number;
  pitch: number;
}

// --- Onset grouping, mirrored from timeline.ts's buildTimeline() -----------
//
// Deliberately duplicated rather than imported/exported from timeline.ts:
// this task's file list is scoped to creating correlate.ts (not modifying
// timeline.ts), and the duplication is small (a fraction-reduction key plus
// a group-by-(measure, notatedOnset) loop) and behaviorally inert to keep in
// lockstep — buildTimeline's own extensive comment explains WHY this exact
// grouping key and sort matter; that reasoning is not repeated here. What
// matters for THIS file: grouping `score` this way, sorted the same way,
// must produce the same ordered sequence of onset groups as the
// `nonRestStepIndices` -> `TimelineEntry[]` pipeline that produced the
// `timeline` argument below, so that `groups[i]` and `timeline[i]` refer to
// the same musical instant for every i.

function parseFraction(value: string): { numerator: number; denominator: number } {
  const [numStr, denStr] = value.split("/");
  const numerator = Number(numStr);
  const denominator = Number(denStr);
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator) || denominator === 0) {
    throw new Error(`invalid notatedOnset fraction: "${value}"`);
  }
  return { numerator, denominator };
}

function gcd(a: number, b: number): number {
  let x = Math.abs(a);
  let y = Math.abs(b);
  while (y !== 0) {
    [x, y] = [y, x % y];
  }
  return x || 1;
}

function normalizedOnsetKey(value: string): string {
  const { numerator, denominator } = parseFraction(value);
  const d = gcd(numerator, denominator);
  return `${numerator / d}/${denominator / d}`;
}

interface OnsetGroup {
  t: number;
  /** The group's events, in the score JSON's own (NOT onset-sorted —
   * verified against a real project in timeline.ts) document order. This
   * document order is exactly what "equal pitches ... resolve by order"
   * (see `matchNotesToEvents` below) falls back to. */
  events: ScoreEvent[];
}

function buildOnsetGroups(score: ScoreJson): OnsetGroup[] {
  const part = score.parts[0];
  if (!part) return [];

  const allGroups: OnsetGroup[] = [];
  for (const measure of part.measures) {
    const groupsByKey = new Map<string, OnsetGroup>();
    for (const ev of measure.events) {
      const key = normalizedOnsetKey(ev.notatedOnset);
      const existing = groupsByKey.get(key);
      if (existing) {
        existing.t = Math.min(existing.t, ev.onsetSeconds);
        existing.events.push(ev);
      } else {
        groupsByKey.set(key, { t: ev.onsetSeconds, events: [ev] });
      }
    }
    allGroups.push(...groupsByKey.values());
  }
  allGroups.sort((a, b) => a.t - b.t);
  return allGroups;
}

// --- Per-step note dedupe + event matching ---------------------------------

type StepNote = StepNoteInfo["notes"][number];

/** Collapses cross-staff duplicates of the SAME physical note (e.g. a
 * guitar's notation staff + TAB staff both drawing the identical pitch for
 * one played note) down to a single representative position, while
 * preserving genuinely distinct same-staff notes that happen to share a
 * pitch (e.g. a unison between two voices on one staff — a real chord
 * member, not a rendering duplicate).
 *
 * Dedupe key is deliberately (pitch, "more than one staffId present") and
 * NOT (pitch, staffId): two notes sharing both pitch AND staffId are kept as
 * separate candidates (see `matchNotesToEvents`'s document-order fallback),
 * but two notes sharing pitch across DIFFERENT staffIds collapse to one —
 * that cross-staff-same-pitch shape is exactly what the guitar exporter's
 * notation+TAB staff pair produces for every note, and is not how a real
 * distinct chord member would ever be encoded (a second voice at the same
 * pitch stays on the SAME staff). */
function dedupeStepNotes(notes: readonly StepNote[]): StepNote[] {
  const byPitch = new Map<number, StepNote[]>();
  for (const note of notes) {
    const existing = byPitch.get(note.pitch);
    if (existing) existing.push(note);
    else byPitch.set(note.pitch, [note]);
  }

  const result: StepNote[] = [];
  for (const group of byPitch.values()) {
    const staffIds = new Set(group.map((note) => note.staffId));
    if (staffIds.size > 1) {
      // Cross-staff duplicate of one physical note — keep one
      // representative (first-seen, i.e. whichever staff OSMD's cursor
      // visits first for this voice entry).
      result.push(group[0]);
    } else {
      // Same staff: every entry is a genuinely distinct note (unison or
      // not), keep them all.
      result.push(...group);
    }
  }
  return result;
}

/** Matches deduped graphical notes to the onset group's score events,
 * pitch-first: for each pitch, the group's events with that pitch are
 * consumed in the score JSON's own document order (a FIFO queue) against
 * the graphical notes of that pitch in THEIR walked order. When a pitch is
 * unambiguous (appears once) this is exact; when a chord has two members at
 * the same pitch (a real unison), neither this file nor the score JSON
 * carries any stronger correlation signal than "both lists are in their own
 * natural order" — so first walked graphical note gets first document-order
 * event, deliberately, as the documented fallback (see task-6-report.md's
 * "equal-pitch chord members" note for why this is the best available
 * signal, not a full disambiguation). */
function matchNotesToEvents(notes: readonly StepNote[], events: readonly ScoreEvent[]): EventPosition[] {
  const eventsByPitch = new Map<number, ScoreEvent[]>();
  for (const ev of events) {
    const existing = eventsByPitch.get(ev.pitch);
    if (existing) existing.push(ev);
    else eventsByPitch.set(ev.pitch, [ev]);
  }

  const result: EventPosition[] = [];
  for (const note of notes) {
    const candidates = eventsByPitch.get(note.pitch);
    const ev = candidates?.shift();
    if (!ev) {
      throw new Error(`correlate: no score event with pitch ${note.pitch} in this onset group`);
    }
    result.push({ eventId: ev.id, x: note.x, y: note.y, pitch: note.pitch });
  }
  return result;
}

/**
 * Builds the full click-to-select position index: one `EventPosition` per
 * real (deduped) note in `score`, positioned at wherever the OSMD walk
 * `walk` found its graphical note.
 *
 * `walk` and `timeline` must come from the SAME cursor walk / score pairing
 * (`timeline` built by `buildTimeline(score, nonRestStepIndices)` where
 * `nonRestStepIndices` and `walk` were collected in the one pass over the
 * cursor — see ScoreView.svelte's `walkCursor`). Throws — rather than
 * silently under-covering — on any structural mismatch between `score`'s
 * own onset grouping and `timeline`/`walk`, mirroring buildTimeline's own
 * fail-loud convention; ScoreView's caller already wraps this in the same
 * try/catch that guards `buildTimeline` (see its `syncError` handling), so
 * a mismatch degrades to "click-to-select unavailable", not a blank score.
 */
export function buildEventPositionIndex(
  walk: readonly StepNoteInfo[],
  timeline: readonly TimelineEntry[],
  score: ScoreJson,
): EventPosition[] {
  const groups = buildOnsetGroups(score);
  if (groups.length !== timeline.length) {
    throw new Error(
      `correlate: found ${groups.length} onset group(s) in score but timeline has ${timeline.length} entr${
        timeline.length === 1 ? "y" : "ies"
      }`,
    );
  }

  const stepNotesByStep = new Map(walk.map((entry) => [entry.step, entry]));

  const result: EventPosition[] = [];
  for (let i = 0; i < timeline.length; i += 1) {
    const step = timeline[i].step;
    const stepNotes = stepNotesByStep.get(step);
    if (!stepNotes) {
      throw new Error(`correlate: no walked note info for non-rest cursor step ${step}`);
    }
    const deduped = dedupeStepNotes(stepNotes.notes);
    result.push(...matchNotesToEvents(deduped, groups[i].events));
  }
  return result;
}

/** The closest `EventPosition` to `(x, y)` (Euclidean, same units as
 * `index`'s own x/y — Notation.svelte always calls this with CSS-px
 * positions from `getEventPositions()` against a CSS-px click point, but
 * this function itself has no opinion on units), or `null` if nothing in
 * `index` is within `maxDistance`. Ties (equal distance) resolve to
 * whichever candidate is encountered first in `index`. */
export function nearestEvent(
  index: readonly EventPosition[],
  x: number,
  y: number,
  maxDistance: number,
): string | null {
  let bestId: string | null = null;
  let bestDistance = Infinity;
  for (const candidate of index) {
    const dx = candidate.x - x;
    const dy = candidate.y - y;
    const distance = Math.sqrt(dx * dx + dy * dy);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestId = candidate.eventId;
    }
  }
  return bestDistance <= maxDistance ? bestId : null;
}
