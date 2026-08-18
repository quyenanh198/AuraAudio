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
  }

  let { zoom, tabVisible }: Props = $props();

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
  }

  function applyZoom(): void {
    if (!osmd) return;
    osmd.zoom = zoom;
    osmd.render();
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

  export function getCursor(): OSMDCursorHandle {
    if (!osmd) throw new Error("loadMusicXml() must resolve before getCursor()");
    const cursor = osmd.cursor;
    return {
      reset: () => cursor.reset(),
      next: () => cursor.next(),
      previous: () => cursor.previous(),
      show: () => cursor.show(),
      hide: () => cursor.hide(),
      isEndReached: () => cursor.iterator.EndReached,
      notesUnderCursor: () => cursor.NotesUnderCursor(),
      gNotesUnderCursor: () => cursor.GNotesUnderCursor(),
      cursorElement: () => cursor.cursorElement ?? null,
    };
  }

  onDestroy(() => {
    osmd = null;
  });
</script>

<div class="notation-host" bind:this={container}></div>

<style>
  .notation-host {
    width: 100%;
  }
</style>
