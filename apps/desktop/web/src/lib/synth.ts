// Synthesized `PlaybackSource` (Task 8) built on `smplr`'s WebAudio Sampler,
// backed by locally bundled MP3 note samples under
// src/assets/soundfonts/{piano,guitar}/ — see task-8-report.md for the
// offline-loading spike (why `smplr` was chosen, how the samples are
// sourced, and how zero-runtime-fetch was proven against a real
// `cargo tauri dev` run).
//
// Scheduling design (task-8 brief, Step 2): an AudioContext-anchored clock.
// `play(from)` anchors `anchorFrom`/`anchorCtxTime` and schedules every
// event with `onsetSeconds >= from` via smplr's `instrument.start({ time,
// duration })`, itself anchored to `ctx.currentTime` — sample-accurate
// regardless of when smplr's internal lookahead scheduler actually
// dispatches the JS callback (its `Scheduler.schedule()` still hands the
// exact absolute `time` to the underlying `AudioBufferSourceNode.start()`).
// Each `start()` call returns a `StopFn` that cancels the note whether or
// not it has started sounding yet (smplr's documented behavior) — retaining
// all of them (`scheduledStops`) is what makes `pause()` an exact,
// glitch-free cutoff, unlike smplr's coarser `instrument.stop()` (which
// only reaches voices already dispatched, not ones still queued in the
// scheduler's lookahead window for a piece scheduled minutes ahead).

import { Sampler, type Smplr } from "smplr";

import type { PlaybackSource } from "./playback";
import type { ScoreEvent, ScoreJson } from "./types";

export type SynthInstrument = "guitar" | "piano";

/** One scheduled note, `at`/`dur` relative to the `from` passed to
 * `schedulePlan` (seconds) — NOT absolute AudioContext time. `play()`
 * converts `at` to an absolute `ctx.currentTime + at` before handing it to
 * smplr. Extracted as a pure function so the scheduling math is unit
 * testable without a real AudioContext (see synth.test.ts). */
export interface SynthScheduledNote {
  at: number;
  dur: number;
  pitch: number;
}

/**
 * Pure scheduling math. Events are NOT necessarily sorted by onset in the
 * score JSON (task-7 finding — see timeline.ts's "Events are NOT
 * pre-sorted" note) — this function sorts its own output by `at` so
 * `play()` can schedule in a deterministic, ascending order.
 */
export function schedulePlan(events: ScoreEvent[], from: number): SynthScheduledNote[] {
  return events
    .filter((ev) => ev.onsetSeconds >= from)
    .map((ev) => ({
      at: ev.onsetSeconds - from,
      dur: Math.max(0, ev.offsetSeconds - ev.onsetSeconds),
      pitch: ev.pitch,
    }))
    .sort((a, b) => a.at - b.at);
}

/** Flattens `score.parts[0]`'s events across all measures — mirrors
 * timeline.ts's single-part assumption (this codebase's scores have
 * exactly one part). */
function flattenEvents(score: ScoreJson): ScoreEvent[] {
  const part = score.parts[0];
  if (!part) return [];
  return part.measures.flatMap((measure) => measure.events);
}

/** Total playback duration: the latest `offsetSeconds` across all events,
 * or 0 for an empty score. */
function scoreDuration(events: ScoreEvent[]): number {
  return events.reduce((max, ev) => Math.max(max, ev.offsetSeconds), 0);
}

// --- Local sample assets -------------------------------------------------
// Bundled via Vite's `import.meta.glob` (`?url`, eager) so every sample
// resolves to a same-origin build asset URL at import time — no runtime
// fetch to any non-127.0.0.1 origin, satisfying the offline constraint.

const PIANO_FILES = import.meta.glob("../assets/soundfonts/piano/*.mp3", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

const GUITAR_FILES = import.meta.glob("../assets/soundfonts/guitar/*.mp3", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

const TONE_NOTE_STEM = /^([A-G])(s?)(-?\d+)$/;
const NATURAL_SEMITONES: Record<string, number> = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };
const SHARP_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

/** Parses the `tonejs-instrument-*-mp3` packages' filename convention
 * (`As4` = A#4 — Tone.js's own "s"-for-sharp note-name scheme, sharps
 * only, no flats) into a MIDI number. */
function toneFileStemToMidi(stem: string): number | null {
  const m = TONE_NOTE_STEM.exec(stem);
  if (!m) return null;
  const base = NATURAL_SEMITONES[m[1]];
  const alt = m[2] === "s" ? 1 : 0;
  const octave = Number(m[3]);
  return base + alt + 12 * (octave + 1);
}

/** Renders a MIDI number back to the sharp-only note-name format smplr's
 * own `noteNameToMidi` parser accepts (`#`, not Tone.js's `s` suffix). */
function midiToSmplrName(midi: number): string {
  const name = SHARP_NAMES[((midi % 12) + 12) % 12];
  const octave = Math.floor(midi / 12) - 1;
  return `${name}${octave}`;
}

/** Builds a smplr `Sampler` buffers map (note name -> local asset URL) from
 * one of the glob results above.
 *
 * Insertion order matters here — not just tidiness. smplr@1.0.0's
 * `samplerToPreset` (the internal function behind `Sampler({ buffers })`)
 * has an indexing bug: it computes `spreadKeyRanges(midiEntries)` — which
 * internally sorts its input by MIDI number and returns pitch/keyRange in
 * *that sorted order* — but then zips the result positionally against the
 * *original, unsorted* `midiEntries` array (`Object.keys(buffers)` order).
 * If the buffers map's keys aren't already MIDI-ascending, every entry gets
 * some OTHER entry's `pitch`, and playing the correct note computes
 * `semitones = requestedMidi - wrongPitch` — occasionally still finite (a
 * wrong, detuned pitch), but for the boundary entries this instead computes
 * `NaN` (confirmed live: `AudioParam.value = NaN` threw inside `new Voice`,
 * silently killing playback — see task-8-report.md's "Surprise" section).
 * `import.meta.glob`'s result (and therefore the raw filename order this
 * function iterates) is alphabetical by filename ("A2, A3, A4, A#2, A#3,
 * ..."), NOT MIDI-ascending, so this bug is hit unconditionally without the
 * explicit sort below. Object key insertion order is preserved by every JS
 * engine for non-integer-like string keys (our note names, e.g. "A2",
 * "C#4"), so sorting entries by MIDI before insertion is a real, reliable
 * fix — not a coincidence relying on iteration-order behavior.
 *
 * Exported (rather than kept module-private) specifically so this fix is
 * regression-tested directly (see synth.test.ts) against a synthetic,
 * deliberately out-of-MIDI-order input — pinning the sort without needing
 * the real glob-derived file lists or a live `Sampler`/`AudioContext`. */
export function buildBuffers(modules: Record<string, string>): Record<string, string> {
  const entries: Array<[midi: number, name: string, url: string]> = [];
  for (const [path, url] of Object.entries(modules)) {
    const stem = path.split("/").pop()?.replace(/\.mp3$/, "") ?? "";
    const midi = toneFileStemToMidi(stem);
    if (midi === null) continue;
    entries.push([midi, midiToSmplrName(midi), url]);
  }
  entries.sort((a, b) => a[0] - b[0]);

  const buffers: Record<string, string> = {};
  for (const [, name, url] of entries) {
    buffers[name] = url;
  }
  return buffers;
}

const INSTRUMENT_BUFFERS: Record<SynthInstrument, Record<string, string>> = {
  piano: buildBuffers(PIANO_FILES),
  guitar: buildBuffers(GUITAR_FILES),
};

/** A `PlaybackSource` plus `dispose()`. `dispose` is not part of the shared
 * interface (the store never disposes sources itself), but ScoreView owns
 * the synth's `AudioContext` lifetime across project navigations and must
 * close the old one before creating a new one. */
export interface SynthPlaybackSource extends PlaybackSource {
  dispose(): void;
}

/** The smplr `Sampler` options that must always be concrete, finite
 * numbers — never omitted (and therefore never `undefined`) — to avoid a
 * real upstream bug in smplr@1.0.0's `Sampler({buffers})` path
 * (`samplerToPreset`): it builds its preset's `defaults` as
 * `{ampRelease: options.decayTime, lpfCutoffHz: options.lpfCutoffHz,
 * detune: options.detune}` verbatim. Omitting these options makes that
 * object hold explicit `undefined` *values* (not absent keys), and smplr's
 * internal merge (`__spreadValues`, a raw `Object.assign`-style copy) does
 * NOT skip `undefined` values the way its own `pickPlaybackParams` helper
 * does elsewhere — so those `undefined`s silently overwrite
 * `PARAM_DEFAULTS`' real fallbacks (`detune: 0`, `lpfCutoffHz: 20000`,
 * `ampRelease: 0.3`), which makes every note's computed `detune` become
 * `NaN` and throw inside `new Voice()` (`AudioParam.value = NaN`) —
 * confirmed by instrumenting smplr's own source directly against a real
 * bundled build; see task-8-report.md's "Surprise" section for the full
 * trace.
 *
 * Extracted as its own pure function (rather than inlined into the
 * `Sampler()` call below) specifically so the workaround itself is
 * regression-tested (see synth.test.ts) without needing a real
 * `AudioContext`/`Sampler` — if a future edit "simplifies" this back down
 * to omitted options, or to `undefined`s, the test catches it before it
 * ships. */
export function synthSamplerDefaults(): { decayTime: number; lpfCutoffHz: number; detune: number } {
  return { decayTime: 0.3, lpfCutoffHz: 20000, detune: 0 };
}

export function createSynthSource(score: ScoreJson, instrument: SynthInstrument): SynthPlaybackSource {
  const events = flattenEvents(score);
  const totalDuration = scoreDuration(events);

  const ctx = new AudioContext();
  const gainNode = ctx.createGain();
  gainNode.connect(ctx.destination);

  const sampler: Smplr = Sampler(ctx, {
    buffers: INSTRUMENT_BUFFERS[instrument],
    destination: gainNode,
    ...synthSamplerDefaults(),
  });

  let scheduledStops: Array<(time?: number) => void> = [];
  let playing = false;
  let anchorFrom = 0;
  let anchorCtxTime = 0;
  let frozenPosition = 0;

  function cancelScheduled(): void {
    for (const stop of scheduledStops) stop();
    scheduledStops = [];
  }

  function computePosition(): number {
    return playing ? anchorFrom + (ctx.currentTime - anchorCtxTime) : frozenPosition;
  }

  function play(from: number): void {
    cancelScheduled();
    void ctx.resume();
    anchorFrom = from;
    anchorCtxTime = ctx.currentTime;
    playing = true;
    for (const note of schedulePlan(events, from)) {
      const stop = sampler.start({
        note: note.pitch,
        time: ctx.currentTime + note.at,
        duration: note.dur > 0 ? note.dur : undefined,
      });
      scheduledStops.push(stop);
    }
  }

  function pause(): void {
    frozenPosition = computePosition();
    cancelScheduled();
    playing = false;
  }

  function seek(t: number): void {
    const wasPlaying = playing;
    cancelScheduled();
    playing = false;
    frozenPosition = t;
    // Mirrors `<audio>`'s native behavior (see `createAudioSource` in
    // playback.ts): assigning a new position during playback keeps playing
    // from there, so a mid-playback scrub re-anchors and reschedules the
    // remaining notes instead of stopping.
    if (wasPlaying) play(t);
  }

  return {
    play,
    pause,
    seek,
    currentTime: computePosition,
    get duration(): number {
      return totalDuration;
    },
    setVolume(v: number): void {
      gainNode.gain.value = v;
    },
    dispose(): void {
      cancelScheduled();
      sampler.dispose();
      void ctx.close();
    },
  };
}
