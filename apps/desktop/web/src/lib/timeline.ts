// Pure sync math between the score-JSON events and OSMD's real cursor
// stepping. Nothing in this file touches OSMD or the DOM — the actual
// cursor walk that produces `nonRestStepIndices` lives in ScoreView.svelte
// (it needs a live OSMDCursorHandle), and this file only consumes its
// output. See task-7-report.md "Timeline / cursor-walk design" for the full
// rationale and the verified OSMD behavior this encodes.

import type { ScoreJson } from "./types";

export interface TimelineEntry {
  /** Audio time (seconds) at which this onset group starts sounding. */
  t: number;
  /** The real OSMD cursor step index (0-based, counting rest steps too)
   * this onset group corresponds to — NOT a synthesized/derived count. */
  step: number;
}

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

/** Reduced-fraction string, so e.g. "1/2" and "2/4" normalize to the same
 * key without floating-point error. */
function normalizedOnsetKey(value: string): string {
  const { numerator, denominator } = parseFraction(value);
  const d = gcd(numerator, denominator);
  return `${numerator / d}/${denominator / d}`;
}

/**
 * Builds the playback timeline: one `TimelineEntry` per real, non-rest OSMD
 * cursor step, in ascending time order.
 *
 * **Events are NOT pre-sorted by onset in the score JSON — verified against
 * a real transcribed project, not assumed.** The brief's original
 * pseudocode (and this function's first draft) assumed `measure.events` is
 * already in ascending-onset document order and only needed a "throw on
 * unsorted input" safety net. Manual verification against a real guitar
 * project's `GET /v1/projects/{id}/score` response disproved that: its one
 * measure's 8 events (4 real chords of 2 notes each) appear in this
 * `id`/array order — `note_00`(t≈1.50) `note_01`(t≈1.00) `note_02`(t≈0.99)
 * `note_03`(t≈0.52) `note_04`(t≈0.49) `note_05`(t≈0.03) `note_06`(t≈1.46)
 * `note_07`(t≈0.01) — which is neither ascending nor descending onset
 * order, and chord partners are not even adjacent (`note_07`+`note_05`
 * share `notatedOnset "0/1"` six array slots apart). Root cause:
 * `quantize.py` enumerates events in whatever order `basic_pitch.predict()`
 * returned them (inference.py), which is not onset-ordered — nothing
 * upstream sorts by time. So this function groups by `(measure,
 * notatedOnset)` using a hash map over the FULL array (no adjacency
 * assumption), then globally sorts the resulting groups by each group's
 * earliest `onsetSeconds` — producing a correctly time-ordered
 * `TimelineEntry[]` regardless of input order. `cursorIndexAt`'s binary
 * search requires this ascending invariant, so it is enforced by
 * construction here rather than merely checked.
 *
 * **Chord/cross-hand grouping key — deliberately `notatedOnset`, not
 * `onsetSeconds`.** `onsetSeconds` (quantize.py: `note.onset_s`) is the
 * raw, unquantized detection time, while `packages/musicxml`'s
 * `_events_to_notes_or_chords` (task-1b, R3) groups same-staff events into
 * one real `<chord/>` by `Fraction(notatedOnset)` equality within a single
 * measure — `notatedOnset` is the quantized, measure-relative position
 * (quantize.py). Two events that are musically simultaneous (a real chord,
 * or same-onset cross-hand piano notes) can have slightly different
 * `onsetSeconds` (detection jitter — confirmed above: `note_07`/`note_05`'s
 * onsets differ by ~0.023s despite being one chord) while sharing the exact
 * same `notatedOnset` — grouping on `onsetSeconds` would fail to collapse
 * them into one step and desync every cursor step after the first chord.
 * Grouping on (measure, notatedOnset) instead matches the exporter's real
 * grouping 1:1, which is what actually determines OSMD's cursor steps.
 *
 * `nonRestStepIndices` is the ordered list of real OSMD cursor step indices
 * that are NOT rest-only steps (obtained by walking the loaded OSMD cursor
 * once from `reset()` to `EndReached`, checking whether every
 * `NotesUnderCursor()` entry is a rest — see ScoreView.svelte's
 * `walkNonRestStepIndices`). Its length must equal the number of distinct
 * onset groups found here: both are counting "how many non-rest musical
 * instants does this score have", just via two different sources of truth
 * (JSON events vs. the real rendered cursor) that must agree. A mismatch
 * means the exporter's chord/rest grouping and this function's grouping
 * have diverged, so it throws rather than silently mis-syncing playback.
 */
export function buildTimeline(score: ScoreJson, nonRestStepIndices: number[]): TimelineEntry[] {
  const part = score.parts[0];
  if (!part) return [];

  interface OnsetGroup {
    t: number;
  }

  const allGroups: OnsetGroup[] = [];

  for (const measure of part.measures) {
    const groupsByKey = new Map<string, OnsetGroup>();
    for (const ev of measure.events) {
      const key = normalizedOnsetKey(ev.notatedOnset);
      const existing = groupsByKey.get(key);
      if (existing) {
        existing.t = Math.min(existing.t, ev.onsetSeconds);
      } else {
        groupsByKey.set(key, { t: ev.onsetSeconds });
      }
    }
    allGroups.push(...groupsByKey.values());
  }

  // Sort globally by earliest onset — see the "events are NOT pre-sorted"
  // note above for why this cannot be assumed from input order.
  allGroups.sort((a, b) => a.t - b.t);

  if (allGroups.length !== nonRestStepIndices.length) {
    throw new Error(
      `timeline/cursor mismatch: found ${allGroups.length} distinct onset(s) but walked ${nonRestStepIndices.length} non-rest cursor step(s)`,
    );
  }

  return allGroups.map((group, i) => ({ t: group.t, step: nonRestStepIndices[i] }));
}

/** Binary search: index of the last entry with `t_i <= t`, or -1 if `t` is
 * before the first entry (or the timeline is empty). */
export function cursorIndexAt(timeline: TimelineEntry[], t: number): number {
  let lo = 0;
  let hi = timeline.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (timeline[mid].t <= t) {
      ans = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return ans;
}

/** How many `next()` calls (since the last `reset()`) land the OSMD cursor
 * on the step this timeline index represents. OSMD's `reset()` already
 * positions the cursor AT step 0 (verified: task-1-report.md's confirmed
 * idiom reads `NotesUnderCursor()` immediately after `reset()`, before any
 * `next()` call) — so reaching step N takes exactly N `next()` calls.
 * `timelineIndex === -1` (cursorIndexAt's "before the first entry") means
 * nothing has started sounding yet: stay at the freshly-reset step 0, i.e.
 * 0 `next()` calls. */
export function desiredNextCallsFor(timeline: TimelineEntry[], timelineIndex: number): number {
  if (timelineIndex < 0) return 0;
  return timeline[timelineIndex].step;
}

export interface CursorMovePlan {
  /** Whether `cursor.reset()` must be called before the `next()` calls. */
  reset: boolean;
  /** How many times to call `cursor.next()` after an optional `reset()`. */
  nextCalls: number;
}

/** The cheapest sequence of cursor operations to move from
 * `performedNextCalls` (next() calls performed since the last reset()) to
 * `desiredNextCalls`: forward is just more `next()` calls; backward
 * requires a `reset()` (OSMD's cursor has no `previous-to-arbitrary-index`
 * jump) followed by `next()` calls from step 0. */
export function planCursorMove(performedNextCalls: number, desiredNextCalls: number): CursorMovePlan {
  if (desiredNextCalls === performedNextCalls) return { reset: false, nextCalls: 0 };
  if (desiredNextCalls > performedNextCalls) {
    return { reset: false, nextCalls: desiredNextCalls - performedNextCalls };
  }
  return { reset: true, nextCalls: desiredNextCalls };
}
