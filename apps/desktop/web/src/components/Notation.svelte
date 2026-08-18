<script lang="ts" module>
  import type { GraphicalNote, Note } from "opensheetmusicdisplay";

  /** The handle Task 7 drives for cursor-synced playback. Method names/shapes
   * mirror Task 1's verified OSMD idioms directly (task-1-report.md
   * "Confirmed cursor idiom"): reset() -> show() -> repeated next(), checked
   * against isEndReached(), reading notesUnderCursor()/gNotesUnderCursor()
   * per step. `cursorElement()` exposes OSMD's own cursor image element (its
   * position tracks the current step) for scrollIntoView-style following. */
  export interface OSMDCursorHandle {
    reset(): void;
    next(): void;
    previous(): void;
    show(): void;
    hide(): void;
    isEndReached(): boolean;
    notesUnderCursor(): Note[];
    gNotesUnderCursor(): GraphicalNote[];
    cursorElement(): HTMLElement | null;
  }
</script>

<script lang="ts">
  import { onDestroy } from "svelte";
  import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";
  import type { Staff } from "opensheetmusicdisplay";

  interface Props {
    /** 1.0 = 100%. Applied via OSMD's `zoom` field + re-render (verified
     * lowercase `zoom`, not `Zoom`, against the installed 2.1.2 typings —
     * the brief's guess at casing was wrong). */
    zoom: number;
    /** Whether the guitar TAB staff (if the loaded score has one) is shown. */
    tabVisible: boolean;
    /** Called after every OSMD `render()` triggered by a prop change
     * (zoom/tab visibility), i.e. every time OSMD is known to have built a
     * brand-new `Cursor` internally. `osmd.render()` does not preserve or
     * re-attach the previous cursor — it constructs a fresh one — so the
     * caller must use this to re-assert cursor visibility/position against
     * the new instance (the `OSMDCursorHandle` from `getCursor()` itself
     * stays valid across this — see the "read osmd.cursor lazily" note
     * below — but its state, e.g. shown/hidden and step position, does
     * not carry over and must be redriven by the caller). Not called for
     * the initial `loadMusicXml()` render, which the caller drives
     * directly afterward via `getCursor()`. */
    onRerender?: () => void;
  }

  let { zoom, tabVisible, onRerender }: Props = $props();

  let container: HTMLDivElement | undefined = $state();
  let osmd: OpenSheetMusicDisplay | null = null;
  let ready = $state(false);

  // Per-staff visibility, not a DOMParser XML filter. OSMD's parsed model
  // exposes a real, public `Staff.isTab: boolean` + a mutable `Staff.Visible:
  // boolean` field (verified in the installed 2.1.2 typings:
  // MusicalScore/VoiceData/Staff.d.ts) on every staff of every
  // `osmd.Sheet.Instruments[i].Staves`. The guitar exporter (task-1b,
  // `packages/musicxml` commit 0126abf) emits notation+TAB as two
  // `PartStaff`s bracketed under one Instrument, so flagging the TAB staff's
  // `Visible = false` and calling `updateGraphic()` + `render()` hides it
  // cleanly through OSMD's own layout — no re-parsing or string surgery on
  // the MusicXML needed. Piano scores have no `isTab` staff, so this is a
  // no-op for them (tabAvailable is false and the toggle is hidden by the
  // caller in that case anyway).
  function tabStaves(): Staff[] {
    if (!osmd?.Sheet) return [];
    return osmd.Sheet.Instruments.flatMap((instrument) => instrument.Staves).filter((staff) => staff.isTab);
  }

  function applyTabVisibility(): void {
    if (!osmd) return;
    const staves = tabStaves();
    if (staves.length === 0) return;
    for (const staff of staves) staff.Visible = tabVisible;
    osmd.updateGraphic();
    osmd.render();
    onRerender?.();
  }

  function applyZoom(): void {
    if (!osmd) return;
    osmd.zoom = zoom;
    osmd.render();
    onRerender?.();
  }

  $effect(() => {
    // Re-read here (not just call the functions) so this effect re-runs
    // whenever `tabVisible` or `ready` changes.
    void tabVisible;
    if (ready) applyTabVisibility();
  });

  $effect(() => {
    void zoom;
    if (ready) applyZoom();
  });

  /** Fetch-and-render is the caller's job (ScoreView owns the MusicXML
   * export fetch); this just takes the already-fetched XML text and mounts/
   * reloads OSMD with it. Safe to call again with new content — creates the
   * OSMD instance lazily on first call once `container` is mounted. */
  export async function loadMusicXml(xml: string): Promise<void> {
    if (!container) throw new Error("Notation container is not mounted yet");
    if (!osmd) {
      osmd = new OpenSheetMusicDisplay(container, { autoResize: false, drawTitle: false });
    }
    ready = false;
    await osmd.load(xml);
    osmd.zoom = zoom;
    osmd.render();
    applyTabVisibility();
    ready = true;
  }

  /** Throws the same "not loaded" error `getCursor()` always has, so every
   * lazy accessor below fails the same way whether called too early or
   * (in principle, e.g. a stray post-unmount callback) too late. */
  function requireOsmd(): OpenSheetMusicDisplay {
    if (!osmd) throw new Error("loadMusicXml() must resolve before getCursor()");
    return osmd;
  }

  export function getCursor(): OSMDCursorHandle {
    if (!osmd) throw new Error("loadMusicXml() must resolve before getCursor()");
    // Every method below reads `requireOsmd().cursor` freshly ON EACH CALL
    // rather than closing over a single `Cursor` captured here — `osmd.render()`
    // (called by applyZoom()/applyTabVisibility() above on every zoom/tab
    // toggle) constructs a BRAND NEW Cursor internally and does not reuse or
    // update the previous one. A handle that captured `osmd.cursor` once
    // would keep driving that discarded Cursor forever after the first
    // re-render: the visible cursor would freeze in place and
    // `cursorElement()` would return a node no longer attached to the
    // document. Reading `.cursor` fresh here means this handle always
    // drives whichever Cursor OSMD currently considers current.
    return {
      reset: () => requireOsmd().cursor.reset(),
      next: () => requireOsmd().cursor.next(),
      previous: () => requireOsmd().cursor.previous(),
      show: () => requireOsmd().cursor.show(),
      hide: () => requireOsmd().cursor.hide(),
      isEndReached: () => requireOsmd().cursor.iterator.EndReached,
      notesUnderCursor: () => requireOsmd().cursor.NotesUnderCursor(),
      gNotesUnderCursor: () => requireOsmd().cursor.GNotesUnderCursor(),
      cursorElement: () => requireOsmd().cursor.cursorElement ?? null,
    };
  }

  onDestroy(() => {
    if (osmd) {
      // Best-effort: Dispose() the current Cursor (hides + removes its
      // element, per the installed 2.1.2 typings) before clear()ing OSMD's
      // own drawn output, so nothing lingers in the DOM after this
      // component unmounts. Guarded in try/catch — `.cursor` can throw if
      // load() never actually completed (e.g. unmounted mid-fetch), and
      // this cleanup must not throw out of onDestroy in that case.
      try {
        osmd.cursor.Dispose();
      } catch {
        // No cursor to dispose — load() never completed.
      }
      osmd.clear();
    }
    osmd = null;
  });
</script>

<div class="notation-host" bind:this={container}></div>

<style>
  .notation-host {
    width: 100%;
  }
</style>
