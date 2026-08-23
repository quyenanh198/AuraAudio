import { describe, expect, it } from "vitest";

import { loadStoredViewMode, resolveViewMode, saveStoredViewMode, type ViewMode } from "./viewMode";

// `resolveViewMode` is the single API this suite tests for both defaulting
// (the "no persisted choice yet" case, `resolveViewMode(null, ...)`) and
// persistence resolution (a `tabAvailable`-gated stored choice) — see its
// own doc comment in viewMode.ts for why there is no separate
// "just the default" helper.

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

describe("loadStoredViewMode / saveStoredViewMode", () => {
  it("returns null when nothing has been stored yet", () => {
    expect(loadStoredViewMode(new FakeStorage())).toBeNull();
  });

  it.each<ViewMode>(["notation", "tab", "both"])("round-trips a saved %s choice", (mode) => {
    const storage = new FakeStorage();
    saveStoredViewMode(mode, storage);
    expect(loadStoredViewMode(storage)).toBe(mode);
  });

  it("falls back to null for an unrecognized stored value", () => {
    const storage = new FakeStorage();
    storage.setItem("auraaudio.viewMode", "everything");
    expect(loadStoredViewMode(storage)).toBeNull();
  });

  it("falls back to null rather than throwing when storage access throws", () => {
    const storage = new ThrowingStorage();
    expect(() => loadStoredViewMode(storage)).not.toThrow();
    expect(loadStoredViewMode(storage)).toBeNull();
    expect(() => saveStoredViewMode("tab", storage)).not.toThrow();
  });
});

describe("resolveViewMode", () => {
  it("always resolves to 'notation' when the project has no TAB staff, regardless of what's stored", () => {
    expect(resolveViewMode(null, false)).toBe("notation");
    expect(resolveViewMode("tab", false)).toBe("notation");
    expect(resolveViewMode("both", false)).toBe("notation");
    expect(resolveViewMode("notation", false)).toBe("notation");
  });

  it("defaults to 'both' for a TAB-capable project with no persisted choice — matches the old toggle's default-on behavior", () => {
    expect(resolveViewMode(null, true)).toBe("both");
  });

  it.each<ViewMode>(["notation", "tab", "both"])("honors a persisted %s choice for a TAB-capable project", (mode) => {
    expect(resolveViewMode(mode, true)).toBe(mode);
  });
});
