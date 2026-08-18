<script lang="ts">
  import { onDestroy, onMount } from "svelte";

  import { api } from "../lib/api";
  import { createCoalescer } from "../lib/coalesce";
  import { createAudioSource, playback } from "../lib/playback";
  import { buildTimeline, cursorIndexAt, desiredNextCallsFor, planCursorMove, type TimelineEntry } from "../lib/timeline";
  import type { ProjectListItem, ScoreJson } from "../lib/types";
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

  /** Walks the real, loaded OSMD cursor once from reset() to EndReached,
   * recording which step indices are NOT rest-only steps. A step is
   * rest-only when every NotesUnderCursor() entry is a rest (or there are
   * none) — confirmed rest steps only ever appear as the MusicXML
   * exporter's explicit gap-filling (task-1b R2); the guitar TAB staff's
   * duplicate per-staff notes never affect this (both staves agree on
   * isRest() for the same musical instant). Leaves the cursor reset to the
   * start when done, ready for playback. */
  function walkNonRestStepIndices(cursor: OSMDCursorHandle): number[] {
    const indices: number[] = [];
    cursor.reset();
    let step = 0;
    while (!cursor.isEndReached()) {
      const notes = cursor.notesUnderCursor();
      const isRestStep = notes.length === 0 || notes.every((note) => note.isRest());
      if (!isRestStep) indices.push(step);
      step += 1;
      cursor.next();
    }
    cursor.reset();
    return indices;
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
    if (!audioEl) return;
    const t = audioEl.currentTime;
    playback.syncPosition(t);
    applyCursorForTime(t);
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

  async function loadScore(): Promise<void> {
    loading = true;
    error = null;
    cursorHandle = null;
    timeline = [];
    lastTimelineIndex = -1;
    performedNextCalls = 0;
    playback.reset();
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
      await notation?.loadMusicXml(xmlText);

      const cursor = notation?.getCursor() ?? null;
      if (cursor && score) {
        const nonRestStepIndices = walkNonRestStepIndices(cursor);
        timeline = buildTimeline(score, nonRestStepIndices);
        cursor.show();
        cursorHandle = cursor;
      }
    } catch (err: unknown) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    if (audioEl) playback.attachSource("recording", createAudioSource(audioEl));
    void loadScore();
  });

  onDestroy(() => {
    stopLoop();
    playback.pause();
    playback.attachSource("recording", null);
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
        <div class="paper">
          <Notation bind:this={notation} zoom={zoomPercent / 100} {tabVisible} />
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

    <Transport onSeek={handleSeek} />
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
