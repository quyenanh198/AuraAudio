import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createAuditioner, loadStoredAuditionEnabled, saveStoredAuditionEnabled } from "./auditioner";

// HONEST SPLIT (see auditioner.ts's own module doc comment): this file
// covers the pure debounce/suppression state machine and the localStorage
// persistence helpers, with a fake `playPitch`/fake storage standing in for
// the real smplr Sampler / real `localStorage`. Whether a real Sampler
// actually produces audible sound is a webview-only concern this suite does
// NOT and cannot cover.

describe("createAuditioner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  function makeDeps(overrides: Partial<{ enabled: boolean; playing: boolean }> = {}) {
    const playPitch = vi.fn();
    const state = { enabled: overrides.enabled ?? true, playing: overrides.playing ?? false };
    return {
      playPitch,
      state,
      deps: {
        isEnabled: () => state.enabled,
        isPlaybackActive: () => state.playing,
        playPitch,
      },
    };
  }

  it("does not fire immediately — waits for the debounce window", () => {
    const { deps, playPitch } = makeDeps();
    const auditioner = createAuditioner(deps);

    auditioner.request(60);

    expect(playPitch).not.toHaveBeenCalled();
  });

  it("fires with the requested pitch after the default 100ms debounce window", () => {
    const { deps, playPitch } = makeDeps();
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    vi.advanceTimersByTime(99);
    expect(playPitch).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(playPitch).toHaveBeenCalledExactlyOnceWith(60);
  });

  it("respects a custom debounceMs", () => {
    const { deps, playPitch } = makeDeps();
    const auditioner = createAuditioner(deps, { debounceMs: 250 });

    auditioner.request(60);
    vi.advanceTimersByTime(249);
    expect(playPitch).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(playPitch).toHaveBeenCalledOnce();
  });

  it("collapses a fast run of requests (holding an arrow key) into a single fire of the LAST pitch", () => {
    const { deps, playPitch } = makeDeps();
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    vi.advanceTimersByTime(30);
    auditioner.request(61);
    vi.advanceTimersByTime(30);
    auditioner.request(62);
    vi.advanceTimersByTime(30);
    auditioner.request(63);

    // Only 30ms has passed since the last request — nothing has fired yet.
    expect(playPitch).not.toHaveBeenCalled();

    vi.advanceTimersByTime(100);
    expect(playPitch).toHaveBeenCalledExactlyOnceWith(63);
  });

  it("does not fire when disabled at request time", () => {
    const { deps, playPitch } = makeDeps({ enabled: false });
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    vi.advanceTimersByTime(100);

    expect(playPitch).not.toHaveBeenCalled();
  });

  it("re-checks `isEnabled` at FIRE time, not request time — muting mid-debounce suppresses a pending request", () => {
    const { deps, state, playPitch } = makeDeps({ enabled: true });
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    state.enabled = false;
    vi.advanceTimersByTime(100);

    expect(playPitch).not.toHaveBeenCalled();
  });

  it("does not fire while playback is active", () => {
    const { deps, playPitch } = makeDeps({ playing: true });
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    vi.advanceTimersByTime(100);

    expect(playPitch).not.toHaveBeenCalled();
  });

  it("re-checks `isPlaybackActive` at FIRE time — starting Play mid-debounce suppresses a pending request", () => {
    const { deps, state, playPitch } = makeDeps({ playing: false });
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    state.playing = true;
    vi.advanceTimersByTime(100);

    expect(playPitch).not.toHaveBeenCalled();
  });

  it("cancel() prevents a pending request from firing", () => {
    const { deps, playPitch } = makeDeps();
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    auditioner.cancel();
    vi.advanceTimersByTime(1000);

    expect(playPitch).not.toHaveBeenCalled();
  });

  it("cancel() is a safe no-op when nothing is pending", () => {
    const { deps } = makeDeps();
    const auditioner = createAuditioner(deps);

    expect(() => auditioner.cancel()).not.toThrow();
  });

  it("a fresh request() after a prior one already fired starts its own independent debounce window", () => {
    const { deps, playPitch } = makeDeps();
    const auditioner = createAuditioner(deps);

    auditioner.request(60);
    vi.advanceTimersByTime(100);
    expect(playPitch).toHaveBeenCalledTimes(1);

    auditioner.request(64);
    vi.advanceTimersByTime(100);
    expect(playPitch).toHaveBeenCalledTimes(2);
    expect(playPitch).toHaveBeenLastCalledWith(64);
  });
});

// --- localStorage persistence (mirrors Sidebar.svelte's PDF-page-size
// preference pattern) --------------------------------------------------

class FakeStorage implements Pick<Storage, "getItem" | "setItem"> {
  private store = new Map<string, string>();
  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }
  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }
}

class ThrowingStorage implements Pick<Storage, "getItem" | "setItem"> {
  getItem(): string | null {
    throw new Error("storage disabled");
  }
  setItem(): void {
    throw new Error("storage disabled");
  }
}

describe("loadStoredAuditionEnabled / saveStoredAuditionEnabled", () => {
  it("defaults to true (ON) when nothing has been stored yet", () => {
    expect(loadStoredAuditionEnabled(new FakeStorage())).toBe(true);
  });

  it("round-trips a saved `false` choice", () => {
    const storage = new FakeStorage();
    saveStoredAuditionEnabled(false, storage);
    expect(loadStoredAuditionEnabled(storage)).toBe(false);
  });

  it("round-trips a saved `true` choice", () => {
    const storage = new FakeStorage();
    saveStoredAuditionEnabled(true, storage);
    expect(loadStoredAuditionEnabled(storage)).toBe(true);
  });

  it("falls back to the default rather than throwing when storage access throws (private window, disabled site data, ...)", () => {
    const storage = new ThrowingStorage();
    expect(() => loadStoredAuditionEnabled(storage)).not.toThrow();
    expect(loadStoredAuditionEnabled(storage)).toBe(true);
    expect(() => saveStoredAuditionEnabled(false, storage)).not.toThrow();
  });

  it("falls back to the default for an unrecognized stored value", () => {
    const storage = new FakeStorage();
    storage.setItem("auraaudio.auditionEnabled", "maybe");
    expect(loadStoredAuditionEnabled(storage)).toBe(true);
  });
});
