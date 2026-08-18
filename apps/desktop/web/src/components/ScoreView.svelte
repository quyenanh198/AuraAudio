<script lang="ts">
  import { onMount } from "svelte";

  import { api } from "../lib/api";
  import type { ProjectListItem, ScoreJson } from "../lib/types";
  import Notation from "./Notation.svelte";
  import Sidebar from "./Sidebar.svelte";

  interface Props {
    projectId: string;
  }

  let { projectId }: Props = $props();

  const MIN_ZOOM_PERCENT = 50;
  const MAX_ZOOM_PERCENT = 200;
  const ZOOM_STEP_PERCENT = 10;

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

  async function loadScore(): Promise<void> {
    loading = true;
    error = null;
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
    } catch (err: unknown) {
      error = err instanceof Error ? err.message : String(err);
    } finally {
      loading = false;
    }
  }

  onMount(() => {
    void loadScore();
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

    <footer class="transport-strip">
      <button type="button" class="play-button" disabled title="Playback lands in Task 7" aria-label="Play">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d="M8 5v14l11-7z" />
        </svg>
      </button>
      <span class="transport-hint">Playback in Task 7</span>
    </footer>
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

  .transport-strip {
    flex: none;
    height: 64px;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 0 20px;
    background: var(--panel);
    border-top: 1px solid var(--border);
  }

  .play-button {
    width: 36px;
    height: 36px;
    border-radius: 50%;
    background: var(--accent);
    color: #1e1d21;
    border: none;
    display: flex;
    align-items: center;
    justify-content: center;
    flex: none;
  }

  .play-button:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .transport-hint {
    font-size: 12px;
    color: var(--dim);
  }
</style>
