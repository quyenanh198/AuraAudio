// Notation view mode: Notation | Tab | Both — replaces the old two-state
// "Tab staff" on/off toggle (Sidebar's VIEW section) with a three-option
// segmented control, matching the existing A4/Letter PDF-page-size control's
// pattern (see Sidebar.svelte's PDF_PAGE_SIZE_OPTIONS/PDF_PAGE_SIZE_STORAGE_KEY
// for the sibling this mirrors — same localStorage-with-try/catch shape).
//
// Pure state/persistence/defaulting logic ONLY. The mechanism this actually
// drives — OSMD's per-staff `Staff.Visible` (verified against the installed
// 2.1.2 typings, MusicalScore/VoiceData/Staff.d.ts) — lives in
// Notation.svelte's `applyViewMode()`, since it needs a live OSMD instance;
// nothing here touches OSMD, the DOM, or localStorage's real global outside
// the injectable `storage` parameter (this project's vitest config runs
// `environment: "node"`, which has no global `localStorage` — see
// vitest.config.ts and auditioner.ts's identical note).

export type ViewMode = "notation" | "tab" | "both";

const VIEW_MODE_STORAGE_KEY = "auraaudio.viewMode";

function isViewMode(value: string | null): value is ViewMode {
  return value === "notation" || value === "tab" || value === "both";
}

/** Reads the persisted view-mode choice, or `null` if nothing valid is
 * stored. Deliberately does NOT apply a default itself — the right default
 * depends on the CURRENT project's instrument (a guitar score's own
 * TAB-staff availability), which this module has no way to know; see
 * `resolveViewMode` below for where that combination happens. */
export function loadStoredViewMode(storage: Pick<Storage, "getItem"> = localStorage): ViewMode | null {
  try {
    const stored = storage.getItem(VIEW_MODE_STORAGE_KEY);
    if (isViewMode(stored)) return stored;
  } catch {
    // Ignore — fall back to null (caller applies its own default).
  }
  return null;
}

export function saveStoredViewMode(mode: ViewMode, storage: Pick<Storage, "setItem"> = localStorage): void {
  try {
    storage.setItem(VIEW_MODE_STORAGE_KEY, mode);
  } catch {
    // Ignore — the picker still reflects the choice for this session even
    // if it can't be persisted for the next one.
  }
}

/** The view mode to apply for a freshly loaded project: the persisted
 * choice IF this project's instrument can actually show a TAB staff,
 * otherwise always "notation" — a "tab"/"both" choice persisted from an
 * earlier GUITAR project is meaningless for a PIANO one (no TAB staff
 * exists to show), and the control itself is disabled for piano (mirroring
 * the old toggle's `disabled={!tabAvailable}`), so it must never present as
 * "selected: Tab" there. `Notation.svelte`'s `applyViewMode()` independently
 * guards the same case (a persisted "tab" briefly reaching it before
 * `tabAvailable` is known), but resolving it here too keeps the CONTROL's
 * own displayed selection honest, not just the rendered staves.
 *
 * With no persisted choice at all, defaults to "both" for guitar (the OLD
 * toggle's default-on/`tabVisible = true` behavior) and "notation" for
 * anything else. */
export function resolveViewMode(stored: ViewMode | null, tabAvailable: boolean): ViewMode {
  if (!tabAvailable) return "notation";
  return stored ?? "both";
}
