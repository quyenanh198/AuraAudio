<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import type { Note } from "opensheetmusicdisplay";

  import { api } from "../lib/api";
  import { createCoalescer } from "../lib/coalesce";
  import { buildEventPositionIndex, type StepNoteInfo } from "../lib/correlate";
  import { editor } from "../lib/editor";
  import { createAudioSource, playback } from "../lib/playback";
  import { createSynthSource, type SynthInstrument, type SynthPlaybackSource } from "../lib/synth";
  import { buildTimeline, cursorIndexAt, desiredNextCallsFor, planCursorMove, type TimelineEntry } from "../lib/timeline";
  import type { ProjectListItem, ScoreJson, ScorePart } from "../lib/types";
  import Notation, { type OSMDCursorHandle } from "./Notation.svelte";
  import Sidebar from "./Sidebar.svelte";
  import Transport from "./Transport.svelte";

  interface Props {
    projectId: string;
  }

  let { projectId }: Props = $props();

  const MIN_ZOOM_PERCENT = 50;
  const MAX_ZOOM_PERCENT = 200;
  const ZOOM_STEP_PERCENT = 10;
  // How often the rAF loop is allowed to re-issue a smooth scrollIntoView()
  // on the cursor element — every step change would otherwise fight itself
  // (a new smooth-scroll starting before the last one settles) on fast runs
  // of short notes.
  const SCROLL_THROTTLE_MS = 200;

  // Explicit generic (not just an LHS annotation) on the nullable $state()
  // calls — with `$state(null)` alone, svelte-check (svelte-check@4.7.3 /
  // svelte@5.56 / typescript@6.0.2, this project's pinned versions) infers
  // the reactive type as the literal `null` and silently ignores the `let`
  // annotation, which only surfaces as "Property 'x' does not exist on type
  // 'never'" wherever the value is narrowed or accessed. Confirmed by a
  // minimal repro before writing this comment.
  let project = $state<ProjectListItem | null>(null);
  let score = $state<ScoreJson | null>(null);
  let loading = $state(true);
  let error = $state<string | null>(null);
  /** CRITICAL 1(b): set when the score itself loaded and rendered fine but
   * building the playback timeline / walking the OSMD cursor failed (e.g.
   * `buildTimeline`'s count-mismatch guard) — kept SEPARATE from `error`
   * on purpose. `error` blanks the whole view (nothing usable rendered);
   * this instead leaves the rendered score up and only disables playback
   * sync, surfaced as a small inline, non-blocking notice. */
  let syncError = $state<string | null>(null);

  let sidebarCollapsed = $state(false);
  let zoomPercent = $state(100);
  let tabVisible = $state(true);

  let notation: Notation | undefined = $state();
  let audioEl: HTMLAudioElement | undefined = $state();

  // Cursor-sync state — not $state: these are imperative bookkeeping for the
  // rAF loop and the seek handler, not reactive UI values. Rebuilt every
  // loadScore() (see there for the reset).
  let cursorHandle: OSMDCursorHandle | null = null;
  let timeline: TimelineEntry[] = [];
  /** The synth `PlaybackSource` for the currently loaded project — owns a
   * real `AudioContext`, so it's explicitly disposed (not just garbage
   * collected) whenever `loadScore()` replaces it or the component
   * unmounts. Rebuilt every `loadScore()` since it's derived from the
   * score's events + instrument. */
  let synthSource: SynthPlaybackSource | null = null;
  /** Index into `timeline` the cursor is currently showing; -1 = "before the
   * first entry" (freshly reset, nothing has sounded yet). */
  let lastTimelineIndex = -1;
  /** next() calls performed since the OSMD cursor's last reset() — see
   * timeline.ts's planCursorMove() for why this (not the OSMD step index
   * itself) is what the move-planner needs. */
  let performedNextCalls = 0;
  let rafId: number | null = null;
  let lastScrollAt = 0;

  function clampZoom(percent: number): number {
    return Math.min(MAX_ZOOM_PERCENT, Math.max(MIN_ZOOM_PERCENT, percent));
  }

  function onZoomChange(percent: number): void {
    zoomPercent = clampZoom(percent);
  }

  function onTabVisibleChange(visible: boolean): void {
    tabVisible = visible;
  }

  function toggleSidebar(): void {
    sidebarCollapsed = !sidebarCollapsed;
  }

  /** `note.halfTone` is real MIDI (middle C = 60) minus 12 (one octave) —
   * NOT the ready-made MIDI number it looks like. Determined empirically,
   * not from source-reading (two earlier readings of the installed 2.1.2
   * bundle's `Pitch` internals — one alleging a flat `+24` offset, one
   * alleging `.halfTone` was unreliable per-note — were both disproven by
   * live data; see task-6-report.md "MIDI pitch" for the full trail).
   * Confirmed against every note manually clicked in both task5-guitar and
   * task5-piano (17 notes total, 0 exceptions) by cross-referencing
   * `note.halfTone + 12` against the exported MusicXML's own `<step>`/
   * `<octave>` for that note and against `ScoreEvent.pitch` for the score
   * event it was expected to correlate to — e.g. task5-guitar's first
   * chord (`<step>E</step><octave>3</octave>` plus a `<chord/>`-tagged
   * `<step>E</step><octave>2</octave>`, i.e. real MIDI 52 and 40) read
   * `note.halfTone` as 40 and 28 respectively — each exactly 12 short. */
  const OSMD_HALFTONE_TO_MIDI_OFFSET = 12;

  function midiPitchOf(note: Note): number {
    return note.halfTone + OSMD_HALFTONE_TO_MIDI_OFFSET;
  }

  /** Walks the real, loaded OSMD cursor once from reset() to EndReached,
   * recording (a) which step indices are NOT rest-only steps, for
   * timeline.ts's `buildTimeline`, and (b) — extending the same walk, not a
   * second one (task-6-brief.md) — every non-rest note's pitch/staff/
   * graphical position at each such step, for correlate.ts's
   * `buildEventPositionIndex`. A step is rest-only when every
   * NotesUnderCursor() entry is a rest (or there are none) — confirmed rest
   * steps only ever appear as the MusicXML exporter's explicit gap-filling
   * (task-1b R2); the guitar TAB staff's duplicate per-staff notes never
   * affect this (both staves agree on isRest() for the same musical
   * instant). `notesUnderCursor()[i]` and `gNotesUnderCursor()[i]` are
   * index-aligned (verified: both iterate the exact same
   * `VoicesUnderCursor().Notes` array in the installed bundle — see
   * task-6-report.md), so zipping them by index is safe. Leaves the cursor
   * reset to the start when done, ready for playback. */
  function walkCursor(cursor: OSMDCursorHandle): { nonRestStepIndices: number[]; stepNotes: StepNoteInfo[] } {
    const nonRestStepIndices: number[] = [];
    const stepNotes: StepNoteInfo[] = [];
    cursor.reset();
    let step = 0;
    while (!cursor.isEndReached()) {
      const notes = cursor.notesUnderCursor();
      const gNotes = cursor.gNotesUnderCursor();
      const isRestStep = notes.length === 0 || notes.every((note) => note.isRest());
      if (!isRestStep) {
        nonRestStepIndices.push(step);
        const stepEntry: StepNoteInfo = { step, notes: [] };
        for (let i = 0; i < notes.length; i += 1) {
          const note = notes[i];
          if (note.isRest()) continue;
          const pos = gNotes[i].PositionAndShape.AbsolutePosition;
          stepEntry.notes.push({
            pitch: midiPitchOf(note),
            staffId: note.ParentStaff.Id,
            x: pos.x,
            y: pos.y,
          });
        }
        stepNotes.push(stepEntry);
      }
      step += 1;
      cursor.next();
    }
    cursor.reset();
    return { nonRestStepIndices, stepNotes };
  }

  /** Re-walks the cursor (see `walkCursor`) and re-runs correlate.ts against
   * the already-built `timeline`/`score` (stable across re-renders — only
   * the OSMD graphical layout, and therefore each note's unit position, can
   * change) to refresh `notation`'s position index, then re-applies
   * whatever is currently selected so the highlight follows the note to its
   * new position. No-op if the cursor/score aren't available, or if the
   * walk/correlate fails — click-to-select degrades silently rather than
   * throwing out of a re-render callback (the existing `syncError` state
   * already covers the load-time failure case; a rebuild failure here just
   * leaves the previous, possibly stale, positions in place). */
  function rebuildEventPositions(): void {
    if (!cursorHandle || !score) return;
    try {
      const { stepNotes } = walkCursor(cursorHandle);
      const positions = buildEventPositionIndex(stepNotes, timeline, score);
      notation?.setEventPositions(positions);
    } catch {
      // Leave whatever positions were already set — degrade gracefully.
    }
    notation?.highlightEvent($editor.selectedEventId);
  }

  /** IMPORTANT 2: `osmd.render()` (triggered by Notation's applyZoom()/
   * applyTabVisibility() on every zoom change or TAB-staff toggle)
   * constructs a brand-new OSMD Cursor internally — the previous one is
   * simply discarded, not updated in place. Notation's `getCursor()`
   * handle reads `osmd.cursor` fresh on every call (see its own comment),
   * so `cursorHandle` itself keeps driving whichever Cursor is current —
   * but a fresh Cursor's own state (visibility, iterator position) does
   * NOT carry over from the old one, so it must be explicitly re-shown and
   * walked back to the position that matches current playback: reset the
   * step-tracking bookkeeping to "nothing applied yet" and re-run
   * applyCursorForTime() for the current playback position. */
  function handleNotationRerender(): void {
    if (!cursorHandle) return;
    // Task 6: rebuild click-to-select positions (and reapply the current
    // highlight at its new position) BEFORE the playback-cursor resync below
    // — `rebuildEventPositions` performs its own full walk (`walkCursor`
    // itself resets/traverses/resets the cursor), and doing that first, then
    // letting the existing resync below move the cursor to where playback
    // actually is, keeps the two concerns (click-to-select positions vs.
    // playback cursor position) from interleaving their cursor.next() calls.
    rebuildEventPositions();
    cursorHandle.reset();
    cursorHandle.show();
    lastTimelineIndex = -1;
    performedNextCalls = 0;
    applyCursorForTime($playback.position);
  }

  function maybeScrollCursorIntoView(): void {
    const now = performance.now();
    if (now - lastScrollAt < SCROLL_THROTTLE_MS) return;
    lastScrollAt = now;
    cursorHandle?.cursorElement()?.scrollIntoView({ block: "center", behavior: "smooth" });
  }

  /** The single place that moves the OSMD cursor to match an audio time —
   * called directly from the rAF loop (while playing, already at most once
   * per frame) and, coalesced, from handleSeek() via scheduleCursorSync()
   * below (so dragging the scrubber moves the cursor even while paused). */
  function applyCursorForTime(t: number): void {
    if (!cursorHandle || timeline.length === 0) return;
    const idx = cursorIndexAt(timeline, t);
    if (idx === lastTimelineIndex) return;
    const desired = desiredNextCallsFor(timeline, idx);
    const plan = planCursorMove(performedNextCalls, desired);
    if (plan.reset) cursorHandle.reset();
    for (let i = 0; i < plan.nextCalls; i += 1) cursorHandle.next();
    performedNextCalls = desired;
    lastTimelineIndex = idx;
    maybeScrollCursorIntoView();
  }

  // Scrubbing fires the range input's `oninput` far faster than rAF ticks
  // during a fast drag — routing every one of those straight into
  // applyCursorForTime() means every event does its own reset()+N×next()
  // OSMD walk (cheap for a short clip, pathological for a long one, and
  // worse dragging backward since that path is always a full reset()).
  // The audio seek + position/time display must stay immediate (that's
  // what makes scrubbing feel responsive), but the OSMD cursor walk itself
  // only needs to reflect the LATEST scrub position, applied at most once
  // per animation frame — a textbook "latest-wins" coalescer.
  const scheduleCursorSync = createCoalescer<number>(applyCursorForTime, (flush) => {
    requestAnimationFrame(flush);
  });

  function handleSeek(t: number): void {
    playback.seek(t);
    scheduleCursorSync(t);
  }

  function tick(): void {
    rafId = requestAnimationFrame(tick);
    // Read through the active `PlaybackSource` (not the `<audio>` element
    // directly) so cursor/position sync works the same whether "recording"
    // or "synth" is selected — see playback.ts's `activeSourceTime()`.
    const t = playback.activeSourceTime();
    if (t === null) return;
    playback.syncPosition(t);
    applyCursorForTime(t);
    // The synth source has no native "ended" event to drive
    // handleAudioEnded() the way the `<audio>` element does — detect
    // reaching the end here instead, for either source.
    if ($playback.playing && $playback.duration > 0 && t >= $playback.duration) {
      playback.pause();
    }
  }

  function startLoop(): void {
    if (rafId !== null) return;
    rafId = requestAnimationFrame(tick);
  }

  function stopLoop(): void {
    if (rafId === null) return;
    cancelAnimationFrame(rafId);
    rafId = null;
  }

  $effect(() => {
    if ($playback.playing) startLoop();
    else stopLoop();
  });

  function handleAudioDurationChange(): void {
    if (!audioEl) return;
    playback.setDuration(Number.isFinite(audioEl.duration) ? audioEl.duration : 0);
  }

  function handleAudioEnded(): void {
    playback.pause();
  }

  /** Defaults to "guitar" for any unrecognized value — mirrors the
   * `tabAvailable` check below, which treats "guitar" as the known,
   * explicitly-handled case. */
  function resolveSynthInstrument(part: ScorePart | undefined): SynthInstrument {
    return part?.instrument === "piano" ? "piano" : "guitar";
  }

  /** CRITICAL 1(b): building the playback timeline and walking the OSMD
   * cursor is kept in its own try/catch, separate from the score
   * fetch/parse/render try/catch in `loadScore()` below. By this point the
   * score has already fetched and rendered successfully — a failure here
   * (e.g. `buildTimeline`'s count-mismatch guard) is a playback-sync-only
   * problem, not a "nothing usable rendered" problem, so it must not blank
   * the view the way `error` does. On failure, `cursorHandle`/`timeline`
   * are left at their already-reset empty state (set by the caller before
   * this runs) so playback code's `if (!cursorHandle...)` guards make
   * sync a safe no-op, and `syncError` drives the inline notice + disabled
   * transport play controls instead. */
  function trySyncPlaybackTimeline(score_: ScoreJson): void {
    try {
      const cursor = notation?.getCursor() ?? null;
      if (!cursor) return;
      const { nonRestStepIndices, stepNotes } = walkCursor(cursor);
      timeline = buildTimeline(score_, nonRestStepIndices);
      cursor.show();
      cursorHandle = cursor;
      // Click-to-select (Task 6) is a separate, non-fatal concern from
      // playback-cursor sync above: a failure here must not set `syncError`
      // (which disables the transport's playback controls) — it should just
      // leave `notation` with no known positions, so clicks find nothing and
      // clear the selection instead of throwing.
      try {
        const positions = buildEventPositionIndex(stepNotes, timeline, score_);
        notation?.setEventPositions(positions);
      } catch {
        // Click-to-select unavailable for this score; playback is unaffected.
      }
    } catch (err: unknown) {
      syncError = err instanceof Error ? err.message : String(err);
    }
  }

  async function loadScore(): Promise<void> {
    loading = true;
    error = null;
    syncError = null;
    cursorHandle = null;
    timeline = [];
    lastTimelineIndex = -1;
    performedNextCalls = 0;
    // A selection from a previous project (or a previous failed load) has no
    // meaning for whatever loads next.
    editor.clearSelection();
    playback.reset();
    if (synthSource) {
      playback.attachSource("synth", null);
      synthSource.dispose();
      synthSource = null;
    }
    try {
      const [projects, scoreJson] = await Promise.all([
        api.listProjects(),
        fetch(api.scoreUrl(projectId)).then(async (resp) => {
          if (!resp.ok) throw new Error(`${resp.status}: ${await resp.text()}`);
          return resp.json() as Promise<ScoreJson>;
        }),
      ]);
      const found = projects.find((item) => item.id === projectId) ?? null;
      if (!found) throw new Error("Project not found.");
      const musicxmlExport = found.exports.find((item) => item.format === "musicxml");
      if (!musicxmlExport) throw new Error("No MusicXML export is available for this project yet.");
      const xmlResp = await fetch(api.exportDownloadUrl(musicxmlExport.id));
      if (!xmlResp.ok) throw new Error(`${xmlResp.status}: ${await xmlResp.text()}`);
      const xmlText = await xmlResp.text();

      project = found;
      score = scoreJson;
      tabVisible = true;
      zoomPercent = 100;

      synthSource = createSynthSource(scoreJson, resolveSynthInstrument(scoreJson.parts[0]));
      playback.attachSource("synth", synthSource);

      await notation?.loadMusicXml(xmlText);
    } catch (err: unknown) {
      error = err instanceof Error ? err.message : String(err);
      loading = false;
      return;
    }
    // The score fetched and rendered successfully at this point — any
    // failure from here on is sync-only (see trySyncPlaybackTimeline) and
    // must not blank the view that just rendered fine.
    if (score) trySyncPlaybackTimeline(score);
    loading = false;
  }

  // IMPORTANT 3: the recording PlaybackSource must track whichever <audio>
  // element is CURRENTLY mounted, not just the one that existed at
  // onMount(). The <audio> element lives in the `{:else}` branch of the
  // error/loading template below — after a load failure, that branch (and
  // its <audio> element) is torn down, and a successful Retry mounts a
  // BRAND NEW <audio> element with a new `audioEl` binding. An onMount-only
  // attach would keep the playback store pointing at the original,
  // now-detached element forever after any Retry. Reading `audioEl` here
  // makes this effect re-run on every such mount/unmount, always attaching
  // (or, when the element goes away, detaching) the source that matches
  // reality. `attachSource` already fully replaces whatever was attached
  // before in one call, so there is no separate "detach then attach" step
  // needed here.
  $effect(() => {
    const el = audioEl;
    playback.attachSource("recording", el ? createAudioSource(el) : null);
  });

  // Single place responsible for "selection changed -> redraw highlight":
  // reacts to every source of a `selectedEventId` change (Notation's own
  // click handler included — it only calls `editor.select()`/
  // `clearSelection()`, never `highlightEvent()` directly) so Task 7's
  // sidebar can select a note the same way and get the same highlight for
  // free. Re-render-triggered position rebuilds (`rebuildEventPositions`)
  // reapply the highlight separately, since a rebuild can move the SAME
  // selected id to a new position without `selectedEventId` itself changing.
  $effect(() => {
    notation?.highlightEvent($editor.selectedEventId);
  });

  onMount(() => {
    void loadScore();
  });

  onDestroy(() => {
    stopLoop();
    scheduleCursorSync.cancel();
    playback.pause();
    playback.attachSource("recording", null);
    playback.attachSource("synth", null);
    synthSource?.dispose();
    synthSource = null;
  });

  let part = $derived(score?.parts[0] ?? null);
  let tabAvailable = $derived(part?.instrument === "guitar");
</script>

<div class="page">
  <header class="topbar">
    <a class="back-link" href="#/">&larr; Projects</a>
    <h1 class="title">{project?.title ?? "Loading…"}</h1>
  </header>

  {#if error}
    <div class="error-panel">
      {error}
      <button type="button" class="retry-link" onclick={() => loadScore()}>Retry</button>
    </div>
  {:else}
    <div class="workspace">
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggleCollapse={toggleSidebar}
        projectTitle={project?.title ?? "score"}
        {part}
        {tabAvailable}
        {tabVisible}
        {onTabVisibleChange}
        {zoomPercent}
        {onZoomChange}
        exports={project?.exports ?? []}
      />

      <main class="paper-area">
        {#if loading}
          <p class="loading-note">Loading score…</p>
        {/if}
        {#if syncError}
          <!-- CRITICAL 1(b): inline, non-blocking — the score above rendered
               fine; only cursor/playback sync failed. Never blanks the view. -->
          <p class="sync-notice" role="status">Playback sync unavailable for this score.</p>
        {/if}
        <div class="paper">
          <Notation
            bind:this={notation}
            zoom={zoomPercent / 100}
            {tabVisible}
            onRerender={handleNotationRerender}
          />
        </div>
      </main>
    </div>

    <audio
      bind:this={audioEl}
      src={api.audioUrl(projectId)}
      preload="metadata"
      class="sr-only-audio"
      ondurationchange={handleAudioDurationChange}
      onloadedmetadata={handleAudioDurationChange}
      onended={handleAudioEnded}
    ></audio>

    <Transport onSeek={handleSeek} playbackSyncAvailable={!syncError} />
  {/if}
</div>

<style>
  .page {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    background: var(--bg);
    color: var(--text);
  }

  .topbar {
    flex: none;
    display: flex;
    align-items: baseline;
    gap: 16px;
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
  }

  .back-link {
    color: var(--accent);
    font-size: 13px;
    text-decoration: none;
    flex: none;
  }

  .back-link:hover {
    text-decoration: underline;
  }

  .title {
    font-size: 15px;
    font-weight: 600;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .error-panel {
    margin: 20px;
    background: rgba(224, 99, 99, 0.1);
    border: 1px solid rgba(224, 99, 99, 0.35);
    color: #e58a8a;
    border-radius: 9px;
    padding: 12px 16px;
    font-size: 13px;
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .retry-link {
    background: none;
    border: 1px solid currentColor;
    color: inherit;
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
  }

  .workspace {
    flex: 1;
    display: flex;
    min-height: 0;
  }

  .paper-area {
    flex: 1;
    min-width: 0;
    overflow: auto;
    padding: 32px clamp(16px, 4vw, 56px);
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 16px;
  }

  .loading-note {
    margin: 0;
    font-size: 13px;
    color: var(--dim);
  }

  /* CRITICAL 1(b): deliberately subdued/inline, unlike .error-panel — this
   * notice must read as "one feature is degraded", not "something broke". */
  .sync-notice {
    margin: 0;
    max-width: 900px;
    width: 100%;
    box-sizing: border-box;
    background: rgba(224, 99, 99, 0.08);
    border: 1px solid rgba(224, 99, 99, 0.25);
    color: var(--dim);
    border-radius: 8px;
    padding: 8px 14px;
    font-size: 12px;
  }

  .paper {
    background: var(--paper);
    color: #1e1d21;
    border-radius: 10px;
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.35);
    padding: 32px clamp(16px, 4vw, 48px);
    width: 100%;
    max-width: 900px;
  }

  .sr-only-audio {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
  }
</style>
