import { beforeEach, describe, expect, it, vi } from "vitest";
import { get, writable } from "svelte/store";

import type { DepsState } from "./deps";
import type { SystemDepsResponse } from "./types";

const isTauriMock = vi.fn();
const invokeMock = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: (...args: unknown[]) => isTauriMock(...args),
  invoke: (...args: unknown[]) => invokeMock(...args),
}));

const recheckMock = vi.fn();
const depsStore = writable<DepsState>({ status: "ok", detail: null, error: null });

vi.mock("./deps", () => ({
  deps: {
    subscribe: depsStore.subscribe,
    check: vi.fn(),
    recheck: (...args: unknown[]) => recheckMock(...args),
  },
}));

function depsResponse(overrides: Partial<SystemDepsResponse> = {}): SystemDepsResponse {
  return {
    ffmpeg: { found: true, version: "7.1", path: "/usr/bin/ffmpeg", source: "path" },
    ffprobe: { found: true, version: "7.1", path: "/usr/bin/ffprobe", source: "path" },
    ytDlp: { found: true, version: "2024.08.06", path: "/usr/bin/yt-dlp", source: "path" },
    allFound: true,
    ...overrides,
  };
}

describe("installDeps", () => {
  beforeEach(() => {
    isTauriMock.mockReset();
    invokeMock.mockReset();
    recheckMock.mockReset();
    depsStore.set({ status: "ok", detail: null, error: null });
  });

  describe("isTauri", () => {
    it("delegates to @tauri-apps/api/core's isTauri", async () => {
      const { isTauri } = await import("./installDeps");
      isTauriMock.mockReturnValue(true);
      expect(isTauri()).toBe(true);
      isTauriMock.mockReturnValue(false);
      expect(isTauri()).toBe(false);
    });
  });

  describe("createInstallStore outside Tauri", () => {
    it("is a no-op that never calls invoke or recheck", async () => {
      isTauriMock.mockReturnValue(false);
      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");

      await store.install();

      expect(get(store).phase).toBe("idle");
      expect(invokeMock).not.toHaveBeenCalled();
      expect(recheckMock).not.toHaveBeenCalled();
    });
  });

  describe("createInstallStore in Tauri: full state machine", () => {
    beforeEach(() => {
      isTauriMock.mockReturnValue(true);
    });

    it("transitions installing -> rechecking -> ok, with the post-install version", async () => {
      let resolveInvoke: (value: unknown) => void = () => {};
      invokeMock.mockReturnValueOnce(
        new Promise((resolve) => {
          resolveInvoke = resolve;
        }),
      );
      recheckMock.mockImplementation(async () => {
        depsStore.set({ status: "ok", detail: depsResponse(), error: null });
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");

      const installPromise = store.install();
      expect(get(store).phase).toBe("installing");

      resolveInvoke({ outcome: "success", exitCode: 0, outputTail: "ffmpeg installed" });
      await installPromise;

      expect(invokeMock).toHaveBeenCalledWith("install_dependency", { name: "ffmpeg" });
      expect(recheckMock).toHaveBeenCalledTimes(1);
      const state = get(store);
      expect(state.phase).toBe("ok");
      expect(state.outcome).toBe("success");
      expect(state.version).toBe("7.1");
    });

    it("passes the correct dependency name for ytDlp", async () => {
      invokeMock.mockResolvedValueOnce({ outcome: "success", exitCode: 0, outputTail: "" });
      recheckMock.mockImplementation(async () => {
        depsStore.set({ status: "ok", detail: depsResponse(), error: null });
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ytDlp");
      await store.install();

      expect(invokeMock).toHaveBeenCalledWith("install_dependency", { name: "ytDlp" });
      expect(get(store).version).toBe("2024.08.06");
    });

    it("goes straight to failed (skipping recheck) when the install command itself fails", async () => {
      invokeMock.mockResolvedValueOnce({
        outcome: "failed",
        exitCode: 1,
        outputTail: "winget: package not found",
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();

      expect(recheckMock).not.toHaveBeenCalled();
      const state = get(store);
      expect(state.phase).toBe("failed");
      expect(state.outcome).toBe("failed");
      expect(state.outputTail).toBe("winget: package not found");
    });

    it("surfaces install.rs's timeout message as ordinary 'failed' output (no dedicated outcome needed)", async () => {
      // install.rs's own INSTALL_TIMEOUT kills a hung installer and still
      // reports outcome: "failed" (not a distinct outcome) with an
      // explanatory outputTail -- this proves that text reaches the store
      // unmodified, which is what the UI renders under its generic
      // "Automatic install failed." headline.
      invokeMock.mockResolvedValueOnce({
        outcome: "failed",
        exitCode: null,
        outputTail: "winget did not finish within 600s and was terminated. Partial output: ",
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();

      const state = get(store);
      expect(state.phase).toBe("failed");
      expect(state.outcome).toBe("failed");
      expect(state.outputTail).toContain("did not finish within");
      expect(recheckMock).not.toHaveBeenCalled();
    });

    it("surfaces 'unsupported' (e.g. ffmpeg on Linux) as a distinct outcome", async () => {
      invokeMock.mockResolvedValueOnce({
        outcome: "unsupported",
        exitCode: null,
        outputTail: "ffmpeg ships via this app's own .deb dependencies on Linux",
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();

      const state = get(store);
      expect(state.phase).toBe("failed");
      expect(state.outcome).toBe("unsupported");
      expect(recheckMock).not.toHaveBeenCalled();
    });

    it("surfaces 'brew_missing' as a distinct outcome (macOS, Homebrew absent)", async () => {
      invokeMock.mockResolvedValueOnce({
        outcome: "brew_missing",
        exitCode: null,
        outputTail: "Homebrew isn't installed",
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();

      expect(get(store).outcome).toBe("brew_missing");
    });

    it("surfaces 'winget_missing' as a distinct outcome (Windows, winget absent)", async () => {
      invokeMock.mockResolvedValueOnce({
        outcome: "winget_missing",
        exitCode: null,
        outputTail: "winget isn't available on this system",
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();

      expect(get(store).outcome).toBe("winget_missing");
      expect(recheckMock).not.toHaveBeenCalled();
    });

    it("fails cleanly when invoke() itself rejects", async () => {
      invokeMock.mockRejectedValueOnce(new Error("IPC channel closed"));

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();

      const state = get(store);
      expect(state.phase).toBe("failed");
      expect(state.outputTail).toBe("IPC channel closed");
      expect(recheckMock).not.toHaveBeenCalled();
    });

    it("reports failed when the command reports success but recheck still shows it missing", async () => {
      // The Windows PATH-refresh trap this feature exists to fix: winget
      // reports success, but the already-running app's env is stale.
      invokeMock.mockResolvedValueOnce({ outcome: "success", exitCode: 0, outputTail: "installed" });
      recheckMock.mockImplementation(async () => {
        depsStore.set({
          status: "missing",
          detail: depsResponse({ ffmpeg: { found: false, version: null, path: null, source: null } }),
          error: null,
        });
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();

      const state = get(store);
      expect(state.phase).toBe("failed");
      expect(state.version).toBeNull();
      expect(state.outputTail).toContain("restart");
    });

    it("reset() returns the store to idle", async () => {
      invokeMock.mockResolvedValueOnce({
        outcome: "failed",
        exitCode: 1,
        outputTail: "boom",
      });

      const { createInstallStore } = await import("./installDeps");
      const store = createInstallStore("ffmpeg");
      await store.install();
      expect(get(store).phase).toBe("failed");

      store.reset();
      expect(get(store)).toEqual({ phase: "idle", outcome: null, outputTail: null, version: null });
    });

    it("two independent stores for different deps don't share state", async () => {
      invokeMock.mockResolvedValueOnce({ outcome: "success", exitCode: 0, outputTail: "" });
      recheckMock.mockImplementation(async () => {
        depsStore.set({ status: "ok", detail: depsResponse(), error: null });
      });

      const { createInstallStore } = await import("./installDeps");
      const ffmpegStore = createInstallStore("ffmpeg");
      const ytDlpStore = createInstallStore("ytDlp");

      await ffmpegStore.install();

      expect(get(ffmpegStore).phase).toBe("ok");
      expect(get(ytDlpStore).phase).toBe("idle");
    });
  });
});
