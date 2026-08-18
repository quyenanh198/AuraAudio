import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const isTauriMock = vi.fn();
const saveMock = vi.fn();
const writeFileMock = vi.fn();

vi.mock("@tauri-apps/api/core", () => ({
  isTauri: (...args: unknown[]) => isTauriMock(...args),
}));

vi.mock("@tauri-apps/plugin-dialog", () => ({
  save: (...args: unknown[]) => saveMock(...args),
}));

vi.mock("@tauri-apps/plugin-fs", () => ({
  writeFile: (...args: unknown[]) => writeFileMock(...args),
}));

describe("isTauri", () => {
  beforeEach(() => {
    isTauriMock.mockReset();
  });

  it("delegates to @tauri-apps/api/core's isTauri", async () => {
    const { isTauri } = await import("./saveExport");
    isTauriMock.mockReturnValue(true);
    expect(isTauri()).toBe(true);

    isTauriMock.mockReturnValue(false);
    expect(isTauri()).toBe(false);
  });
});

describe("saveExport", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    isTauriMock.mockReset();
    saveMock.mockReset();
    writeFileMock.mockReset();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("returns 'fallback' without touching the network when not running in Tauri", async () => {
    isTauriMock.mockReturnValue(false);
    global.fetch = vi.fn();

    const { saveExport } = await import("./saveExport");
    const result = await saveExport("http://127.0.0.1:8317/v1/exports/abc/download", "My-Song.musicxml");

    expect(result).toBe("fallback");
    expect(global.fetch).not.toHaveBeenCalled();
    expect(saveMock).not.toHaveBeenCalled();
    expect(writeFileMock).not.toHaveBeenCalled();
  });

  it("happy path: fetches bytes, opens the save dialog, and writes the chosen path", async () => {
    isTauriMock.mockReturnValue(true);
    const bytes = new Uint8Array([1, 2, 3, 4]);
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      arrayBuffer: async () => bytes.buffer,
    });
    saveMock.mockResolvedValue("/home/user/Music/My-Song.musicxml");
    writeFileMock.mockResolvedValue(undefined);

    const { saveExport } = await import("./saveExport");
    const result = await saveExport("http://127.0.0.1:8317/v1/exports/abc/download", "My-Song.musicxml");

    expect(result).toBe("saved");
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        defaultPath: "My-Song.musicxml",
        filters: [{ name: "MUSICXML", extensions: ["musicxml"] }],
      }),
    );
    expect(writeFileMock).toHaveBeenCalledTimes(1);
    const [writtenPath, writtenBytes] = writeFileMock.mock.calls[0];
    expect(writtenPath).toBe("/home/user/Music/My-Song.musicxml");
    expect(writtenBytes).toEqual(bytes);
  });

  it("returns 'cancelled' and never calls writeFile when the user dismisses the dialog", async () => {
    isTauriMock.mockReturnValue(true);
    global.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      arrayBuffer: async () => new ArrayBuffer(4),
    });
    saveMock.mockResolvedValue(null);

    const { saveExport } = await import("./saveExport");
    const result = await saveExport("http://127.0.0.1:8317/v1/exports/abc/download", "My-Song.mid");

    expect(result).toBe("cancelled");
    expect(writeFileMock).not.toHaveBeenCalled();
  });

  it("throws a readable error when the fetch itself fails (non-ok response)", async () => {
    isTauriMock.mockReturnValue(true);
    global.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      arrayBuffer: async () => new ArrayBuffer(0),
    });

    const { saveExport } = await import("./saveExport");

    await expect(saveExport("http://127.0.0.1:8317/v1/exports/abc/download", "My-Song.mid")).rejects.toThrow(
      /500/,
    );
    expect(saveMock).not.toHaveBeenCalled();
    expect(writeFileMock).not.toHaveBeenCalled();
  });

  it("throws when fetch itself rejects (network failure)", async () => {
    isTauriMock.mockReturnValue(true);
    global.fetch = vi.fn().mockRejectedValue(new Error("network down"));

    const { saveExport } = await import("./saveExport");

    await expect(saveExport("http://127.0.0.1:8317/v1/exports/abc/download", "My-Song.mid")).rejects.toThrow(
      "network down",
    );
  });
});
