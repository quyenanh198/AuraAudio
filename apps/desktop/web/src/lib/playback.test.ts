import { describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";

import { createPlaybackStore, type PlaybackSource } from "./playback";

function fakeSource(overrides: Partial<PlaybackSource> = {}): PlaybackSource {
  return {
    play: vi.fn(),
    pause: vi.fn(),
    seek: vi.fn(),
    currentTime: vi.fn(() => 0),
    duration: 0,
    setVolume: vi.fn(),
    ...overrides,
  };
}

describe("playback store", () => {
  it("starts at position 0, duration 0, not playing, recording source, full volume", () => {
    const store = createPlaybackStore();
    expect(get(store)).toEqual({ position: 0, duration: 0, playing: false, source: "recording", volume: 1 });
  });

  it("seek clamps to [0, duration]", () => {
    const store = createPlaybackStore();
    store.setDuration(10);

    store.seek(-5);
    expect(get(store).position).toBe(0);

    store.seek(50);
    expect(get(store).position).toBe(10);

    store.seek(4.2);
    expect(get(store).position).toBe(4.2);
  });

  it("seek clamps to 0 when duration is still 0 (no metadata loaded yet)", () => {
    const store = createPlaybackStore();
    store.seek(3);
    expect(get(store).position).toBe(0);
  });

  it("seek forwards the clamped value to the active source", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    store.attachSource("recording", recording);
    store.setDuration(10);

    store.seek(20);

    expect(recording.seek).toHaveBeenCalledWith(10);
  });

  it("play() marks playing and starts the active source from the current position", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    store.attachSource("recording", recording);
    store.setDuration(10);
    store.seek(3);

    store.play();

    expect(get(store).playing).toBe(true);
    expect(recording.play).toHaveBeenCalledWith(3);
  });

  it("pause() marks not-playing and syncs position from the source's currentTime()", () => {
    const store = createPlaybackStore();
    const recording = fakeSource({ currentTime: vi.fn(() => 4.5) });
    store.attachSource("recording", recording);
    store.setDuration(10);
    store.play();

    store.pause();

    expect(get(store).playing).toBe(false);
    expect(get(store).position).toBe(4.5);
    expect(recording.pause).toHaveBeenCalled();
  });

  it("setSource is inert when no source is attached for the target kind", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    store.attachSource("recording", recording);
    store.setDuration(10);
    store.seek(6);
    store.play();

    store.setSource("synth"); // never attached — must be a no-op

    const state = get(store);
    expect(state.source).toBe("recording");
    expect(state.position).toBe(6);
    expect(state.playing).toBe(true);
  });

  it("setSource preserves position and playing across a real swap", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    const synth = fakeSource();
    store.attachSource("recording", recording);
    store.attachSource("synth", synth);
    store.setDuration(10);
    store.seek(7);
    store.play();
    vi.mocked(recording.play).mockClear();

    store.setSource("synth");

    const state = get(store);
    expect(state.source).toBe("synth");
    expect(state.position).toBe(7);
    expect(state.playing).toBe(true);
    expect(recording.pause).toHaveBeenCalled();
    expect(synth.seek).toHaveBeenCalledWith(7);
    expect(synth.play).toHaveBeenCalledWith(7);
  });

  it("setSource does not (re)start the new source when not playing", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    const synth = fakeSource();
    store.attachSource("recording", recording);
    store.attachSource("synth", synth);
    store.setDuration(10);
    store.seek(2);

    store.setSource("synth");

    expect(synth.seek).toHaveBeenCalledWith(2);
    expect(synth.play).not.toHaveBeenCalled();
    expect(get(store).playing).toBe(false);
  });

  it("setSource to the current source is a no-op", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    store.attachSource("recording", recording);

    store.setSource("recording");

    expect(recording.pause).not.toHaveBeenCalled();
    expect(recording.seek).not.toHaveBeenCalled();
  });

  it("setVolume clamps to [0, 1] and forwards to the active source", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    store.attachSource("recording", recording);

    store.setVolume(1.5);
    expect(get(store).volume).toBe(1);
    expect(recording.setVolume).toHaveBeenCalledWith(1);

    store.setVolume(-0.5);
    expect(get(store).volume).toBe(0);
    expect(recording.setVolume).toHaveBeenCalledWith(0);
  });

  it("attachSource applies the store's current volume to a newly attached source", () => {
    const store = createPlaybackStore();
    store.setVolume(0.3);
    const recording = fakeSource();

    store.attachSource("recording", recording);

    expect(recording.setVolume).toHaveBeenCalledWith(0.3);
  });

  it("attachSource(kind, null) detaches — the source is no longer driven", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    store.attachSource("recording", recording);
    store.attachSource("recording", null);

    store.play();
    store.seek(1);

    expect(recording.play).not.toHaveBeenCalled();
    expect(recording.seek).not.toHaveBeenCalled();
  });

  it("reset() returns position/duration/playing to their initial values", () => {
    const store = createPlaybackStore();
    const recording = fakeSource();
    store.attachSource("recording", recording);
    store.setDuration(10);
    store.seek(5);
    store.play();

    store.reset();

    const state = get(store);
    expect(state.position).toBe(0);
    expect(state.duration).toBe(0);
    expect(state.playing).toBe(false);
    expect(recording.pause).toHaveBeenCalled();
  });

  it("reset() preserves volume and source", () => {
    const store = createPlaybackStore();
    store.setVolume(0.4);

    store.reset();

    const state = get(store);
    expect(state.volume).toBe(0.4);
    expect(state.source).toBe("recording");
  });

  it("activeSourceTime() returns null when no source is attached for the current kind", () => {
    const store = createPlaybackStore();
    expect(store.activeSourceTime()).toBeNull();
  });

  it("activeSourceTime() reads the active source's currentTime()", () => {
    const store = createPlaybackStore();
    const recording = fakeSource({ currentTime: vi.fn(() => 2.5) });
    store.attachSource("recording", recording);

    expect(store.activeSourceTime()).toBe(2.5);
  });

  it("activeSourceTime() follows setSource() to the newly active source", () => {
    const store = createPlaybackStore();
    const recording = fakeSource({ currentTime: vi.fn(() => 1) });
    const synth = fakeSource({ currentTime: vi.fn(() => 9) });
    store.attachSource("recording", recording);
    store.attachSource("synth", synth);

    store.setSource("synth");

    expect(store.activeSourceTime()).toBe(9);
  });

  it("play()/pause()/seek()/setVolume() are safe no-ops with no source attached at all", () => {
    const store = createPlaybackStore();
    expect(() => {
      store.play();
      store.pause();
      store.seek(1);
      store.setVolume(0.5);
    }).not.toThrow();
  });
});
