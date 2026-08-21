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

describe("savePdfBytes", () => {
  const originalFetch = global.fetch;

  beforeEach(() => {
    isTauriMock.mockReset();
    saveMock.mockReset();
    writeFileMock.mockReset();
    global.fetch = vi.fn();
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  it("Tauri: opens the save dialog with a PDF filter and writes the chosen path (never touches fetch)", async () => {
    isTauriMock.mockReturnValue(true);
    saveMock.mockResolvedValue("/home/user/Music/My-Song.pdf");
    writeFileMock.mockResolvedValue(undefined);
    const bytes = new Uint8Array([37, 80, 68, 70]); // "%PDF"

    const { savePdfBytes } = await import("./saveExport");
    const result = await savePdfBytes(bytes, "My-Song.pdf");

    expect(result).toBe("saved");
    expect(saveMock).toHaveBeenCalledWith(
      expect.objectContaining({
        defaultPath: "My-Song.pdf",
        filters: [{ name: "PDF", extensions: ["pdf"] }],
      }),
    );
    expect(writeFileMock).toHaveBeenCalledWith("/home/user/Music/My-Song.pdf", bytes);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("Tauri: returns 'cancelled' and never calls writeFile when the user dismisses the dialog", async () => {
    isTauriMock.mockReturnValue(true);
    saveMock.mockResolvedValue(null);

    const { savePdfBytes } = await import("./saveExport");
    const result = await savePdfBytes(new Uint8Array([1]), "My-Song.pdf");

    expect(result).toBe("cancelled");
    expect(writeFileMock).not.toHaveBeenCalled();
  });

  describe("outside Tauri (plain-browser Blob download)", () => {
    // This project's vitest config runs `environment: "node"`
    // (vitest.config.ts) -- there is no real `document`/`URL.
    // createObjectURL` here, unlike an actual browser. `document` is
    // stubbed with a minimal fake (createElement -> a fake anchor,
    // body.appendChild) so downloadBlobInBrowser()'s DOM calls can be
    // observed directly, the same spirit as exportPdf.test.ts's fake
    // SVGElement stand-ins -- this is an orchestration test (which DOM
    // calls happen, in what order, with what arguments), not a real
    // end-to-end download, which is webview/browser-only.
    let anchor: { href: string; download: string; style: Record<string, string>; click: ReturnType<typeof vi.fn>; remove: ReturnType<typeof vi.fn> };
    let createElementMock: ReturnType<typeof vi.fn>;
    let appendChildMock: ReturnType<typeof vi.fn>;
    let createObjectURLSpy: ReturnType<typeof vi.spyOn>;
    let revokeObjectURLSpy: ReturnType<typeof vi.spyOn>;

    beforeEach(() => {
      vi.useFakeTimers();
      anchor = { href: "", download: "", style: {}, click: vi.fn(), remove: vi.fn() };
      createElementMock = vi.fn().mockReturnValue(anchor);
      appendChildMock = vi.fn();
      vi.stubGlobal("document", { createElement: createElementMock, body: { appendChild: appendChildMock } });
      createObjectURLSpy = vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:fake-pdf-url");
      revokeObjectURLSpy = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    });

    afterEach(() => {
      vi.unstubAllGlobals();
      createObjectURLSpy.mockRestore();
      revokeObjectURLSpy.mockRestore();
      vi.useRealTimers();
    });

    it("builds a Blob object URL, clicks a DOM-attached <a download>, and returns 'fallback'", async () => {
      isTauriMock.mockReturnValue(false);
      const bytes = new Uint8Array([37, 80, 68, 70]);

      const { savePdfBytes } = await import("./saveExport");
      const result = await savePdfBytes(bytes, "My-Song.pdf");

      expect(result).toBe("fallback");
      expect(createObjectURLSpy).toHaveBeenCalledTimes(1);
      const blobArg = createObjectURLSpy.mock.calls[0][0] as Blob;
      expect(blobArg.type).toBe("application/pdf");
      expect(createElementMock).toHaveBeenCalledWith("a");
      expect(anchor.href).toBe("blob:fake-pdf-url");
      expect(anchor.download).toBe("My-Song.pdf");
      expect(appendChildMock).toHaveBeenCalledWith(anchor);
      expect(anchor.click).toHaveBeenCalledTimes(1);
      expect(anchor.remove).toHaveBeenCalledTimes(1);
      expect(global.fetch).not.toHaveBeenCalled();
      expect(saveMock).not.toHaveBeenCalled();
      expect(writeFileMock).not.toHaveBeenCalled();
    });

    it("revokes the object URL shortly after (not synchronously, to avoid racing the browser's own download start)", async () => {
      isTauriMock.mockReturnValue(false);

      const { savePdfBytes } = await import("./saveExport");
      await savePdfBytes(new Uint8Array([1]), "My-Song.pdf");

      expect(revokeObjectURLSpy).not.toHaveBeenCalled();
      vi.runAllTimers();
      expect(revokeObjectURLSpy).toHaveBeenCalledWith("blob:fake-pdf-url");
    });
  });
});
