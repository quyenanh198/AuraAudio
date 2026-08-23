<script lang="ts" module>
  import { Instrument } from "opensheetmusicdisplay";
  import type { GraphicalNote, Note } from "opensheetmusicdisplay";

  /** Module-load-time patch for a real, confirmed OSMD 2.1.2 defect that
   * blocks the view-mode feature's playback cursor (Task 3) — see
   * `setStaffAndVoicesVisible`'s own doc comment below for the full
   * mechanism this fixes and why toggling `Voice.Visible` (not just
   * `Staff.Visible`) is required in the first place.
   *
   * THE DEFECT: the installed bundle's `Instrument` class defines
   * `get Visible(){return this.voices.length>0 && this.Voices[0].Visible}`
   * — i.e. "instrument visible" is hardcoded to just its FIRST voice's own
   * flag, not "any staff/voice of this instrument is visible" (confirmed
   * directly against `opensheetmusicdisplay.min.js`'s own source — no
   * non-minified build ships the implementation to link here). For a
   * SINGLE-staff instrument this is harmless (Voices[0] IS the only
   * voice). For a MULTI-staff instrument (this app's guitar
   * notation+TAB pair, `packages/musicxml/src/musicxml/export.py`'s
   * `_build_guitar_notation_and_tab`) it's a real bug: hiding the
   * notation staff's own voices (to fix the CURSOR ambiguity below) also
   * hides `Instrument.Voices[0]` specifically (notation's staff is
   * created first, so its own first voice always ends up at index 0),
   * which makes `Instrument.Visible` -- and therefore `Staff.isVisible()`
   * (`this.Visible && this.ParentInstrument.Visible`, for EVERY staff of
   * this instrument, including the one still meant to be shown) -- return
   * `false` for the whole instrument. Confirmed via a real e2e run: with
   * only this patch missing, `walkCursor`'s `note.ParentStaff.isVisible()`
   * check (ScoreView.svelte) started rejecting even the visible TAB
   * staff's own notes, and OSMD's internal `Cursor.update()` /
   * `findVisibleGraphicalMeasure` (which also gate on `.isVisible()`)
   * broke the same way.
   *
   * THE FIX: this app never hides a whole `Instrument` (only individual
   * `Staff`s within one, via view-mode) -- so `Instrument.Visible` can
   * simply always be `true` here, sidestepping the buggy `Voices[0]`
   * dependency entirely, without touching anything this app doesn't
   * already rely on (`Staff.Visible`, which this patch never reads).
   * `configurable: true` on class accessors is the default (not
   * explicitly frozen anywhere in the bundle), so `defineProperty` is a
   * legitimate override, not a hack around a sealed property -- and this
   * runs exactly once per module load (`Instrument.prototype` is shared
   * across every `Instrument` instance OSMD ever constructs, present or
   * future, in this app). */
  Object.defineProperty(Instrument.prototype, "Visible", {
    get: () => true,
    configurable: true,
  });

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

  import { editor } from "../lib/editor";
  import { nearestEvent, type EventPosition } from "../lib/correlate";
  import type { ViewMode } from "../lib/viewMode";

  interface Props {
    /** 1.0 = 100%. Applied via OSMD's `zoom` field + re-render (verified
     * lowercase `zoom`, not `Zoom`, against the installed 2.1.2 typings —
     * the brief's guess at casing was wrong). */
    zoom: number;
    /** Which staves show: standard notation only, TAB only, or both — see
     * `applyViewMode()` below for the mechanism. For a score with no TAB
     * staff at all (e.g. piano), every mode renders identically (full
     * display) — `applyViewMode()` itself guards this, so an inconsistent
     * value reaching this prop (e.g. a persisted "tab" from an earlier
     * guitar project applied before the caller knows this project has no
     * TAB staff) can never blank the score. */
    viewMode: ViewMode;
    /** Called after every OSMD `render()` triggered by a prop change
     * (zoom/view mode), i.e. every time OSMD is known to have built a
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

  let { zoom, viewMode, onRerender }: Props = $props();

  let container: HTMLDivElement | undefined = $state();
  let osmd: OpenSheetMusicDisplay | null = null;
  let ready = $state(false);

  // --- Click-to-select (Task 6) ---------------------------------------
  //
  // Unit conversion lives HERE and only here (per task-6-brief.md): verified
  // against the installed 2.1.2 bundle (no non-minified source/typings ship
  // the implementation, so this was confirmed by grepping
  // node_modules/opensheetmusicdisplay/build/opensheetmusicdisplay.min.js —
  // see task-6-report.md for the exact matched snippets).
  // `Cursor.updateWidthAndStyle` positions the cursor's own <img> with
  // `top = 10 * unit.y * osmd.zoom + "px"`, `left = 10 * unit.x * osmd.zoom
  // + "px"` (both the ThinLeft and CurrentArea cursor types use this same
  // `10 * value * zoom` formula) — so CSS px = 10 * (OSMD engraving unit) *
  // osmd.zoom, for both x and y. The historical "10 px per unit" rule the
  // brief warned not to trust turned out to be exactly right; `osmd.zoom` is
  // this component's own `zoom` prop (verified lowercase field, see
  // `applyZoom` below). The cursor's <img> is appended into the SAME element
  // `new OpenSheetMusicDisplay(container, ...)` was constructed with — i.e.
  // this component's own `container` div — as its `position: relative`
  // first child (OSMD sets `position: relative` on that inner wrapper
  // itself), so an absolutely-positioned child of `.notation-host` using
  // this exact formula lands in the same coordinate space OSMD's own cursor
  // uses, with no separate offset bookkeeping needed.
  const OSMD_UNIT_TO_PX = 10;

  /** Generous click-radius (CSS px at 100% zoom, scaled by current zoom same
   * as note positions are) around a notehead's recorded position — chosen
   * empirically to comfortably cover a notehead + a little slop, not derived
   * from OSMD's own notehead metrics (which aren't exposed per-note). */
  const CLICK_MAX_DISTANCE_PX = 22;

  /** Highlight box half-extents, in OSMD engraving units (same units
   * `EventPosition.x`/`y` are recorded in before this component's own px
   * conversion) — sized to comfortably bracket a notehead plus a bit of
   * stem/ledger area above and below, since `PositionAndShape.AbsolutePosition`
   * is a note's bounding-box reference point, not its visual center. */
  const HIGHLIGHT_HALF_WIDTH_UNITS = 1.1;
  const HIGHLIGHT_ABOVE_UNITS = 1.2;
  const HIGHLIGHT_BELOW_UNITS = 3.2;

  /** Raw-unit positions handed down by ScoreView's cursor walk + correlate
   * step (see `setEventPositions`) — NOT reactive `$state`: this is
   * imperative bookkeeping consumed by `getEventPositions()`/
   * `highlightEvent()` on demand, not something this component renders
   * directly off of. Rebuilt by the caller on every load and on every
   * OSMD re-render (`onRerender`), since OSMD re-layout (e.g. toggling the
   * TAB staff) can shift these unit positions even though zoom alone never
   * does. */
  let eventPositionsUnits: EventPosition[] = [];

  interface HighlightBox {
    left: number;
    top: number;
    width: number;
    height: number;
  }

  let highlightBox = $state<HighlightBox | null>(null);

  function unitToPx(unit: number): number {
    return unit * OSMD_UNIT_TO_PX * zoom;
  }

  /** Called by ScoreView after every (re-)walk of the cursor, handing down
   * the correlate.ts output in raw OSMD units. */
  export function setEventPositions(positions: EventPosition[]): void {
    eventPositionsUnits = positions;
  }

  /** CSS-px positions for every known event, recomputed against the CURRENT
   * `zoom` on every call (not cached) so a zoom change alone — no re-walk
   * needed, unit values don't change with zoom — is reflected immediately. */
  export function getEventPositions(): EventPosition[] {
    return eventPositionsUnits.map((p) => ({ ...p, x: unitToPx(p.x), y: unitToPx(p.y) }));
  }

  /** Draws (or, for `null`, clears) the amber selection overlay at the given
   * event's position. Safe to call with an id not present in
   * `eventPositionsUnits` (e.g. a stale selection surviving a score edit
   * that removed the event) — clears the overlay rather than throwing. */
  export function highlightEvent(eventId: string | null): void {
    if (eventId === null) {
      highlightBox = null;
      return;
    }
    const match = eventPositionsUnits.find((p) => p.eventId === eventId);
    if (!match) {
      highlightBox = null;
      return;
    }
    highlightBox = {
      left: unitToPx(match.x - HIGHLIGHT_HALF_WIDTH_UNITS),
      top: unitToPx(match.y - HIGHLIGHT_ABOVE_UNITS),
      width: unitToPx(HIGHLIGHT_HALF_WIDTH_UNITS * 2),
      height: unitToPx(HIGHLIGHT_ABOVE_UNITS + HIGHLIGHT_BELOW_UNITS),
    };
  }

  /** Container click -> nearest known note within `CLICK_MAX_DISTANCE_PX` ->
   * select it; click on empty space (nothing within range, or nothing
   * walked yet) clears the selection. Selecting here does not draw the
   * highlight directly — ScoreView owns that sync (it subscribes to
   * `editor`'s `selectedEventId` for both this click-driven path and any
   * future non-click selection source, e.g. Task 7's sidebar), keeping one
   * place responsible for "selection changed -> redraw highlight". */
  function handleContainerClick(event: MouseEvent): void {
    if (!container) return;
    const rect = container.getBoundingClientRect();
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    const id = nearestEvent(getEventPositions(), x, y, CLICK_MAX_DISTANCE_PX * zoom);
    if (id) editor.select(id);
    else editor.clearSelection();
  }

  // Per-staff visibility, not a DOMParser XML filter. OSMD's parsed model
  // exposes a real, public `Staff.isTab: boolean` + a mutable `Staff.Visible:
  // boolean` field (verified in the installed 2.1.2 typings:
  // MusicalScore/VoiceData/Staff.d.ts) on every staff of every
  // `osmd.Sheet.Instruments[i].Staves`. The guitar exporter (task-1b,
  // `packages/musicxml` commit 0126abf) emits notation+TAB as two
  // `PartStaff`s bracketed under one Instrument, so flagging a staff's own
  // `Visible` and calling `updateGraphic()` + `render()` hides it cleanly
  // through OSMD's own layout — no re-parsing or string surgery on the
  // MusicXML needed. Extends the SAME two-state mechanism the old "Tab
  // staff" toggle used (it only ever flagged the TAB staff) to the new
  // three-way view mode by *also* flagging the notation staff for the new
  // "tab"-only case — one staff-visibility mechanism, symmetric across both
  // staff kinds, not two different ones.
  function tabStaves(): Staff[] {
    if (!osmd?.Sheet) return [];
    return osmd.Sheet.Instruments.flatMap((instrument) => instrument.Staves).filter((staff) => staff.isTab);
  }

  function notationStaves(): Staff[] {
    if (!osmd?.Sheet) return [];
    return osmd.Sheet.Instruments.flatMap((instrument) => instrument.Staves).filter((staff) => !staff.isTab);
  }

  /** Piano scores (and any other score with no TAB staff) have no `isTab`
   * staff at all — self-guarding no-op here (rather than relying on the
   * caller's own `tabAvailable` check) means an inconsistent `viewMode`
   * value reaching this component (e.g. a "tab"/"notation" choice persisted
   * from an earlier GUITAR project, applied for one render before the
   * caller's own instrument check catches up — see viewMode.ts's
   * `resolveViewMode`) can never hide EVERY staff and blank the score: with
   * no real TAB staff to distinguish, every mode renders identically (full
   * display), matching the OLD toggle's existing no-op-for-piano behavior
   * (`if (staves.length === 0) return;`) exactly. */
  /** Sets a staff's own `Visible` (drives layout/ink — the mechanism the
   * doc comment above already covers) AND every one of its `Voice`s'
   * `Visible` (drives which staff's `VoiceEntry`s the OSMD *cursor*
   * considers, a SEPARATE filter OSMD applies independently of staff-level
   * visibility — see this function's own doc comment below for why both
   * are required). */
  function setStaffAndVoicesVisible(staff: Staff, visible: boolean): void {
    staff.Visible = visible;
    for (const voice of staff.Voices) voice.Visible = visible;
  }

  /** Real-render-probe regression, found via the view-mode e2e test's own
   * new playback-cursor assertion (Task 3): flagging ONLY `Staff.Visible`
   * (as this function did originally) hides the right noteheads and
   * reflows layout correctly (Task 2's own e2e height assertions pass) —
   * but leaves OSMD's playback CURSOR permanently stuck invisible
   * (`cursorElement.style.display` stays `"none"` forever, confirmed via a
   * real DOM inspection: `top`/`left` never get set even after repeated
   * `next()` calls during real playback) in Tab-only/Notation-only mode.
   * Root cause, traced into the installed 2.1.2 bundle's own minified
   * `Cursor.update()`: for the normal (non-edge-case) step, it resolves
   * the cursor's on-screen position from `iterator.
   * CurrentVisibleVoiceEntries()` — mapping EACH entry to a graphical
   * staff entry and taking the leftmost. That iterator method filters by
   * `VoiceEntry.ParentVoice.Visible` (a SEPARATE flag from `Staff.
   * Visible`, confirmed in the bundle's own `getVisibleEntries`), NOT by
   * staff visibility — so with only `Staff.Visible` toggled, BOTH the
   * notation staff's AND the TAB staff's own real, independent voice
   * entries (this app's guitar exporter genuinely duplicates the note
   * data onto two real `PartStaff`s — see `packages/musicxml/src/
   * musicxml/export.py`'s `_build_guitar_notation_and_tab`, not an
   * OSMD-auto-generated TAB) still come back from that call in a HIDDEN
   * staff's mode, including the one belonging to the now-invisible staff.
   * Its graphical-entry lookup then fails (that staff's own graphical
   * staff entries aren't part of the currently-rendered layout), the
   * internal position computation bails out early, and the cursor is left
   * exactly where `init()` put it: hidden, at no position, forever —
   * regardless of `show()`/`next()` calls afterward, since every one of
   * them hits the same early-out. Toggling each hidden staff's `Voice`s'
   * own `Visible` too (via `setStaffAndVoicesVisible`) makes
   * `CurrentVisibleVoiceEntries()` return ONLY the one entry belonging to
   * the staff actually being displayed — the ambiguity (and the bug) goes
   * away, since there's no longer a hidden-staff candidate for that lookup
   * to pick and fail on. Verified via a real e2e run: synth playback
   * started in Tab-only mode, cursor visible and its rendered
   * bounding-box position advances as playback runs.
   *
   * REQUIRES the module-level `Instrument.prototype.Visible` patch above
   * this component's `<script module>` block: without it, hiding a
   * multi-staff instrument's voices this way trips a SEPARATE OSMD defect
   * (`Instrument.Visible` incorrectly deriving from only its first voice)
   * that makes `Staff.isVisible()` wrongly report `false` for every staff
   * of the instrument, including the one still meant to be shown — see
   * that patch's own doc comment for the full trail. Found via this exact
   * function's own e2e regression: fixing the cursor this way, alone,
   * broke click-to-select (`.event-highlight` stopped appearing in
   * Tab-only mode) until that second patch was added too. */
  function applyViewMode(): void {
    if (!osmd) return;
    const tab = tabStaves();
    if (tab.length === 0) return;
    const notation = notationStaves();
    const showNotation = viewMode !== "tab";
    const showTab = viewMode !== "notation";
    for (const staff of notation) setStaffAndVoicesVisible(staff, showNotation);
    for (const staff of tab) setStaffAndVoicesVisible(staff, showTab);
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
    // Re-read here (not just call the function) so this effect re-runs
    // whenever `viewMode` or `ready` changes.
    void viewMode;
    if (ready) applyViewMode();
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
    applyViewMode();
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
    // (called by applyZoom()/applyViewMode() above on every zoom/view-mode
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

<!-- Click-to-select is inherently spatial (which note is nearest the click
     point) — there's no keyboard equivalent to bind here, the same way
     there isn't for e.g. a canvas. -->
<!-- svelte-ignore a11y_click_events_have_key_events -->
<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="notation-wrapper">
  <div class="notation-host" bind:this={container} onclick={handleContainerClick}></div>
  {#if highlightBox}
    <!-- DOM-ownership fix (found via the view-mode e2e test): `.event-highlight`
         must NOT be a child of `.notation-host` — OSMD's `render()` (called by
         applyZoom()/applyViewMode() on every zoom change or view-mode switch,
         i.e. every re-render after the FIRST) directly manipulates `container`'s
         DOM (it clears and redraws its own SVG output there), which silently
         drops any Svelte-rendered sibling nodes living INSIDE that same
         container along with it — the reactive `highlightBox` state stays
         perfectly correct (confirmed by direct inspection), but the actual DOM
         node Svelte thinks it already inserted is gone, so nothing ever
         reappears despite highlightEvent() being re-run afterward and computing
         the right box. A LATENT bug for zoom too (this is the exact same
         re-render path), just never one an existing e2e assertion happened to
         check right after a zoom change — the view-mode work is what surfaced
         it. Moving this div to be a SIBLING of `.notation-host` (both inside
         `.notation-wrapper`, which now owns the `position: relative` this
         needs) keeps it entirely outside whatever `container` gets cleared to,
         while `unitToPx()`'s coordinate math is unaffected: `.notation-host`
         has no margin/border/padding of its own, so its top-left is still
         pixel-identical to `.notation-wrapper`'s. -->
    <div
      class="event-highlight"
      style="left: {highlightBox.left}px; top: {highlightBox.top}px; width: {highlightBox.width}px; height: {highlightBox.height}px;"
    ></div>
  {/if}
</div>

<style>
  .notation-wrapper {
    width: 100%;
    position: relative;
  }

  .notation-host {
    width: 100%;
    position: relative;
  }

  /* Deliberately distinct from OSMD's own playback cursor (a thin vertical
   * bar) — this is a filled, glowing amber rectangle so a selected note
   * reads as "selected", not "currently playing". Never touches OSMD's SVG
   * internals — purely an absolutely-positioned overlay div, and — see the
   * DOM-ownership comment above — deliberately NOT a child of `.notation-host`
   * any more, so OSMD's own direct DOM writes there can never silently drop it. */
  .event-highlight {
    position: absolute;
    box-sizing: border-box;
    border: 2px solid #d99a4e;
    border-radius: 5px;
    background: rgba(217, 154, 78, 0.18);
    box-shadow: 0 0 6px 1px rgba(217, 154, 78, 0.55);
    pointer-events: none;
  }
</style>
