// Playback store contract: {position, duration, playing, source, volume}
// with play(), pause(), seek(t), setSource(s), setVolume(v).
//
// `PlaybackSource` is the seam that lets multiple playback backends share
// one store: `createAudioSource()` below wraps an `HTMLAudioElement` behind
// it, and Task 8's `createSynthSource()` (src/lib/synth.ts) wraps a WebAudio
// scheduler behind the same interface. The store itself never touches
// `HTMLAudioElement` (or WebAudio) directly — only `PlaybackSource`. That's
// also what keeps this file's store logic unit-testable without a real
// `<audio>` element (a plain object satisfying the interface stands in for
// one).

import { writable, type Readable } from "svelte/store";

export interface PlaybackSource {
  play(from: number): void;
  pause(): void;
  seek(t: number): void;
  currentTime(): number;
  duration: number;
  setVolume(v: number): void;
}

export type PlaybackSourceKind = "recording" | "synth";

export interface PlaybackState {
  position: number;
  duration: number;
  playing: boolean;
  source: PlaybackSourceKind;
  volume: number;
}

export interface PlaybackStore extends Readable<PlaybackState> {
  play(): void;
  pause(): void;
  /** Clamps to [0, duration]. */
  seek(t: number): void;
  /** Inert (no-op) if no source has been `attachSource()`'d for `kind` yet
   * — this is how Transport's disabled "Synth" toggle stays safe to click
   * even before Task 8 registers a synth source. Preserves `position` and
   * `playing` across the swap. */
  setSource(kind: PlaybackSourceKind): void;
  /** Clamps to [0, 1]. */
  setVolume(v: number): void;
  /** Registers (or clears, with `null`) the concrete `PlaybackSource` for
   * one `kind`. Owned by the component wiring the real `<audio>` element
   * (or, later, the synth) — the store never constructs sources itself. */
  attachSource(kind: PlaybackSourceKind, src: PlaybackSource | null): void;
  /** Driven by the `<audio>` element's `loadedmetadata`/`durationchange`. */
  setDuration(d: number): void;
  /** Driven by the owning component's rAF loop while playing. */
  syncPosition(t: number): void;
  /** The currently active source's live `currentTime()`, or `null` if no
   * source is attached for the current `kind`. The owning component's rAF
   * loop reads this (instead of e.g. an `<audio>` element directly) so
   * cursor/position sync works identically for whichever `PlaybackSource`
   * (recording or synth) is currently selected — the loop doesn't need to
   * know which kind is active. */
  activeSourceTime(): number | null;
  /** Back to position 0 / duration 0 / not playing, for reuse across project
   * navigations (this store is a module-level singleton). Leaves `volume`
   * and `source` as the user left them. */
  reset(): void;
}

const INITIAL_STATE: PlaybackState = {
  position: 0,
  duration: 0,
  playing: false,
  source: "recording",
  volume: 1,
};

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function createPlaybackStore(): PlaybackStore {
  const { subscribe, update } = writable<PlaybackState>({ ...INITIAL_STATE });
  const sources: Partial<Record<PlaybackSourceKind, PlaybackSource>> = {};

  // Local mirror of the store's state, kept in sync via the subscription
  // below, so methods can read the current values synchronously (svelte
  // stores don't otherwise offer a synchronous "get" outside svelte/store's
  // `get()` helper, which would add a dependency for no benefit here).
  let state: PlaybackState = { ...INITIAL_STATE };
  subscribe((s) => {
    state = s;
  });

  function active(): PlaybackSource | null {
    return sources[state.source] ?? null;
  }

  function play(): void {
    update((s) => ({ ...s, playing: true }));
    active()?.play(state.position);
  }

  function pause(): void {
    const src = active();
    src?.pause();
    const position = src ? src.currentTime() : state.position;
    update((s) => ({ ...s, playing: false, position }));
  }

  function seek(t: number): void {
    const clamped = clamp(t, 0, Math.max(0, state.duration));
    update((s) => ({ ...s, position: clamped }));
    active()?.seek(clamped);
  }

  function setSource(kind: PlaybackSourceKind): void {
    if (kind === state.source) return;
    const target = sources[kind];
    if (!target) return; // inert — e.g. "synth" before Task 8 attaches one
    const wasPlaying = state.playing;
    const position = state.position;
    active()?.pause();
    update((s) => ({ ...s, source: kind }));
    target.seek(position);
    if (wasPlaying) target.play(position);
  }

  function setVolume(v: number): void {
    const clamped = clamp(v, 0, 1);
    update((s) => ({ ...s, volume: clamped }));
    active()?.setVolume(clamped);
  }

  function attachSource(kind: PlaybackSourceKind, src: PlaybackSource | null): void {
    if (src) {
      sources[kind] = src;
      src.setVolume(state.volume);
    } else {
      delete sources[kind];
    }
  }

  function setDuration(d: number): void {
    update((s) => ({ ...s, duration: Number.isFinite(d) && d >= 0 ? d : 0 }));
  }

  function syncPosition(t: number): void {
    update((s) => ({ ...s, position: t }));
  }

  function activeSourceTime(): number | null {
    const src = active();
    return src ? src.currentTime() : null;
  }

  function reset(): void {
    active()?.pause();
    update((s) => ({ ...s, position: 0, duration: 0, playing: false }));
  }

  return {
    subscribe,
    play,
    pause,
    seek,
    setSource,
    setVolume,
    attachSource,
    setDuration,
    syncPosition,
    activeSourceTime,
    reset,
  };
}

/** Wraps a real `HTMLAudioElement` behind `PlaybackSource` — the only place
 * in this file that touches the DOM audio API. */
export function createAudioSource(audio: HTMLAudioElement): PlaybackSource {
  return {
    play(from: number): void {
      audio.currentTime = from;
      void audio.play();
    },
    pause(): void {
      audio.pause();
    },
    seek(t: number): void {
      audio.currentTime = t;
    },
    currentTime(): number {
      return audio.currentTime;
    },
    get duration(): number {
      return Number.isFinite(audio.duration) ? audio.duration : 0;
    },
    setVolume(v: number): void {
      audio.volume = v;
    },
  };
}

export const playback = createPlaybackStore();
