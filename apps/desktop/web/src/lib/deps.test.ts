import { beforeEach, describe, expect, it, vi } from "vitest";
import { get } from "svelte/store";

import type { SystemDepsResponse } from "./types";

const getSystemDepsMock = vi.fn();

vi.mock("./api", () => ({
  api: {
    getSystemDeps: (...args: unknown[]) => getSystemDepsMock(...args),
  },
}));

function depsResponse(overrides: Partial<SystemDepsResponse> = {}): SystemDepsResponse {
  return {
    ffmpeg: { found: true, version: "6.1.1" },
    ffprobe: { found: true, version: "6.1.1" },
    allFound: true,
    ...overrides,
  };
}

describe("deps store", () => {
  beforeEach(() => {
    vi.resetModules();
    getSystemDepsMock.mockReset();
  });

  it("starts in the checking state before check() resolves", async () => {
    let resolveFetch: (value: SystemDepsResponse) => void = () => {};
    getSystemDepsMock.mockReturnValueOnce(
      new Promise<SystemDepsResponse>((resolve) => {
        resolveFetch = resolve;
      }),
    );

    const { deps } = await import("./deps");
    const checkPromise = deps.check();
    expect(get(deps).status).toBe("checking");

    resolveFetch(depsResponse());
    await checkPromise;
  });

  it("transitions to ok when both binaries are found", async () => {
    getSystemDepsMock.mockResolvedValueOnce(depsResponse());

    const { deps } = await import("./deps");
    await deps.check();

    const state = get(deps);
    expect(state.status).toBe("ok");
    expect(state.detail?.allFound).toBe(true);
    expect(state.error).toBeNull();
  });

  it("transitions to missing when a binary is not found", async () => {
    getSystemDepsMock.mockResolvedValueOnce(
      depsResponse({ ffmpeg: { found: false, version: null }, allFound: false }),
    );

    const { deps } = await import("./deps");
    await deps.check();

    const state = get(deps);
    expect(state.status).toBe("missing");
    expect(state.detail?.ffmpeg.found).toBe(false);
    expect(state.detail?.ffprobe.found).toBe(true);
  });

  it("treats a network/API failure as a distinct error state, not missing", async () => {
    // A failed CHECK (network error, backend not up yet) is not proof
    // ffmpeg is absent -- it must be distinguishable from a successful
    // check that reports allFound: false, so the UI can render "couldn't
    // check" instead of misleadingly claiming ffmpeg isn't installed.
    getSystemDepsMock.mockRejectedValueOnce(new Error("boom"));

    const { deps } = await import("./deps");
    await deps.check();

    const state = get(deps);
    expect(state.status).toBe("error");
    expect(state.status).not.toBe("missing");
    expect(state.detail).toBeNull();
    expect(state.error).toBe("boom");
  });

  it("recheck() can recover from the error state to ok", async () => {
    getSystemDepsMock.mockRejectedValueOnce(new Error("boom"));

    const { deps } = await import("./deps");
    await deps.check();
    expect(get(deps).status).toBe("error");

    getSystemDepsMock.mockResolvedValueOnce(depsResponse());
    await deps.recheck();

    const state = get(deps);
    expect(state.status).toBe("ok");
    expect(state.error).toBeNull();
  });

  it("recheck() re-runs the same check and can flip missing back to ok", async () => {
    getSystemDepsMock
      .mockResolvedValueOnce(depsResponse({ ffmpeg: { found: false, version: null }, allFound: false }))
      .mockResolvedValueOnce(depsResponse());

    const { deps } = await import("./deps");
    await deps.check();
    expect(get(deps).status).toBe("missing");

    await deps.recheck();
    expect(get(deps).status).toBe("ok");
    expect(getSystemDepsMock).toHaveBeenCalledTimes(2);
  });

  it("recheck() sets status back to checking while the new request is in flight", async () => {
    getSystemDepsMock.mockResolvedValueOnce(
      depsResponse({ ffmpeg: { found: false, version: null }, allFound: false }),
    );
    const { deps } = await import("./deps");
    await deps.check();
    expect(get(deps).status).toBe("missing");

    let resolveFetch: (value: SystemDepsResponse) => void = () => {};
    getSystemDepsMock.mockReturnValueOnce(
      new Promise<SystemDepsResponse>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    const recheckPromise = deps.recheck();
    expect(get(deps).status).toBe("checking");
    resolveFetch(depsResponse());
    await recheckPromise;
    expect(get(deps).status).toBe("ok");
  });
});

describe("detectPlatform", () => {
  it("detects windows from the user agent", async () => {
    const { detectPlatform } = await import("./deps");
    expect(detectPlatform({ userAgent: "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", platform: "" })).toBe(
      "windows",
    );
  });

  it("detects windows from navigator.platform when userAgent doesn't mention it", async () => {
    const { detectPlatform } = await import("./deps");
    expect(detectPlatform({ userAgent: "some UA", platform: "Win32" })).toBe("windows");
  });

  it("detects macos from the user agent", async () => {
    const { detectPlatform } = await import("./deps");
    expect(detectPlatform({ userAgent: "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)", platform: "MacIntel" })).toBe(
      "macos",
    );
  });

  it("falls back to linux for anything else", async () => {
    const { detectPlatform } = await import("./deps");
    expect(detectPlatform({ userAgent: "Mozilla/5.0 (X11; Linux x86_64)", platform: "Linux x86_64" })).toBe(
      "linux",
    );
  });
});

describe("installCommandFor", () => {
  it("maps windows to the winget command", async () => {
    const { installCommandFor } = await import("./deps");
    expect(installCommandFor("windows")).toBe("winget install Gyan.FFmpeg");
  });

  it("maps macos to the brew command", async () => {
    const { installCommandFor } = await import("./deps");
    expect(installCommandFor("macos")).toBe("brew install ffmpeg");
  });

  it("maps linux to the apt command", async () => {
    const { installCommandFor } = await import("./deps");
    expect(installCommandFor("linux")).toBe("sudo apt install ffmpeg");
  });
});
