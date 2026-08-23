// Note audition ("nghe để kiểm tra nốt" — listen to check the note): a
// brief pitch preview fired when a note is selected on the score, or when
// its pitch changes via the Inspector's pitch stepper or the ArrowUp/
// ArrowDown keyboard shortcuts. This module owns ONLY the pure state
// machine — debouncing rapid requests and deciding whether a debounced
// request is still allowed to fire by the time its timer elapses — never
// the DOM/WebAudio/OSMD. The actual sound comes from the SAME smplr
// instrument the playback source already loads (see synth.ts's
// `SynthPlaybackSource.auditionNote`, a short-lived voice that shares the
// existing `Sampler`/`AudioContext` rather than standing up a second audio
// stack), which ScoreView.svelte wires in as this module's `playPitch` dep.
//
// Deliberately a plain closure factory (mirrors playback.ts/synth.ts's own
// "factory function returning an interface" shape), not a Svelte store —
// nothing here is UI state a component template reads reactively; it is
// pure imperative bookkeeping a component's script drives (same category as
// ScoreView.svelte's own `cursorHandle`/`timeline` fields).
//
// HONEST TEST SPLIT: everything in this file — debounce timing, the
// enabled/playback-suppression gates, re-checked at FIRE time rather than
// request time — is exercised by auditioner.test.ts with fake timers and a
// fake `playPitch`. Whether a real smplr `Sampler.start()` call actually
// produces audible sound is NOT covered by any automated test in this
// codebase (there is no headless WebAudio harness here) — that is a
// webview-only, manually-verified concern, same as every other real-audio
// path in synth.ts.

export interface AuditionerOptions {
  /** Debounce window in ms — a fast run of requests (e.g. holding ArrowUp
   * to step several semitones) collapses to just the LAST one, fired this
   * long after the last `request()` call. Defaults to 100ms per the
   * brief's "holding ↑ doesn't machine-gun" requirement. */
  debounceMs?: number;
}

export interface AuditionerDeps {
  /** Whether the user currently has audition enabled (Sidebar's "Audition
   * notes" mute toggle). Read fresh at FIRE time, not captured once at
   * `request()` time — muting mid-debounce must suppress the pending
   * request, not just future ones. */
  isEnabled(): boolean;
  /** Whether the real transport is currently playing. An audition must
   * never sound while actual playback is running — it would either double
   * up with the real note or clash with a musically unrelated position.
   * Also read fresh at fire time, for the same reason as `isEnabled`: Play
   * starting during the debounce window must still suppress it. */
  isPlaybackActive(): boolean;
  /** The one real side effect: actually sounds `pitch` briefly. */
  playPitch(pitch: number): void;
}

export interface Auditioner {
  /** Requests an audition of `pitch`. Cancels any not-yet-fired pending
   * request first, so a fast run of calls (selection changes, repeated
   * keypresses) only ever results in the LAST pitch sounding, once. */
  request(pitch: number): void;
  /** Cancels any pending (not-yet-fired) request without sounding it —
   * call on project switch / component teardown so a stale timer can never
   * fire into a disposed or already-replaced synth. Safe to call with
   * nothing pending. */
  cancel(): void;
}

const DEFAULT_DEBOUNCE_MS = 100;

export function createAuditioner(deps: AuditionerDeps, options: AuditionerOptions = {}): Auditioner {
  const debounceMs = options.debounceMs ?? DEFAULT_DEBOUNCE_MS;
  let timer: ReturnType<typeof setTimeout> | null = null;

  function cancel(): void {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
  }

  function request(pitch: number): void {
    cancel();
    timer = setTimeout(() => {
      timer = null;
      // Both gates are re-checked HERE, not when `request()` was called —
      // either can have changed during the debounce window (most notably:
      // the user pressed Play while a debounced audition was still
      // pending, which must silently suppress it rather than sound a note
      // out of step with real playback).
      if (!deps.isEnabled() || deps.isPlaybackActive()) return;
      deps.playPitch(pitch);
    }, debounceMs);
  }

  return { request, cancel };
}

// --- "Audition notes" mute toggle persistence ---------------------------
//
// Same localStorage pattern as Sidebar.svelte's existing PDF-page-size
// preference (see its PDF_PAGE_SIZE_STORAGE_KEY): read/write wrapped in
// try/catch (a private window, disabled site data, or a first run with
// nothing ever saved should all just fall back to the default rather than
// throw), storage is injectable (defaults to the real `localStorage`) so
// this is testable without a DOM/browser environment — this project's
// vitest config runs `environment: "node"` (see vitest.config.ts), which has
// no global `localStorage`.

const AUDITION_ENABLED_STORAGE_KEY = "auraaudio.auditionEnabled";

/** Default ON, per the brief. */
const AUDITION_ENABLED_DEFAULT = true;

export function loadStoredAuditionEnabled(storage: Pick<Storage, "getItem"> = localStorage): boolean {
  try {
    const stored = storage.getItem(AUDITION_ENABLED_STORAGE_KEY);
    if (stored === "true") return true;
    if (stored === "false") return false;
  } catch {
    // Ignore — fall back to the default below.
  }
  return AUDITION_ENABLED_DEFAULT;
}

export function saveStoredAuditionEnabled(enabled: boolean, storage: Pick<Storage, "setItem"> = localStorage): void {
  try {
    storage.setItem(AUDITION_ENABLED_STORAGE_KEY, String(enabled));
  } catch {
    // Ignore — the toggle still reflects the choice for this session even
    // if it can't be persisted for the next one.
  }
}
