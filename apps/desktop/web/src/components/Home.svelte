<script lang="ts">
  import { onDestroy, onMount } from "svelte";

  import { api } from "../lib/api";
  import { deps, detectPlatform, installCommandFor } from "../lib/deps";
  import { projects } from "../lib/projects";
  import type { ProjectListItem } from "../lib/types";

  const ACCEPTED_EXTENSIONS = [".wav", ".mp3", ".m4a"];

  type RowStatus = "succeeded" | "failed" | "running" | "none";

  let fileInput: HTMLInputElement | undefined = $state();
  let isDragOver = $state(false);
  let pendingFile: File | null = $state(null);
  let creating = $state(false);
  let creationError: string | null = $state(null);
  let retryingId: string | null = $state(null);
  let retryError: string | null = $state(null);
  // ProjectJobSummary (the shape /v1/projects returns per row) only carries
  // id/status/stage/progress — no error_detail. Fetch it lazily per failed
  // job via GET /v1/jobs/{id} so the chip's tooltip can show the real
  // reason instead of just the bare status.
  let errorDetails: Record<string, string> = $state({});
  let depsCopyFeedback = $state(false);

  const installCommand = installCommandFor(detectPlatform());

  onMount(() => {
    void projects.refresh();
    void deps.check();
  });

  function missingBinaryNames(): string {
    const detail = $deps.detail;
    if (!detail) return "ffmpeg/ffprobe";
    const missing = [
      !detail.ffmpeg.found ? "ffmpeg" : null,
      !detail.ffprobe.found ? "ffprobe" : null,
    ].filter((name): name is string => name !== null);
    return missing.length > 0 ? missing.join(" and ") : "ffmpeg/ffprobe";
  }

  async function copyInstallCommand(): Promise<void> {
    try {
      await navigator.clipboard.writeText(installCommand);
      depsCopyFeedback = true;
      setTimeout(() => {
        depsCopyFeedback = false;
      }, 2000);
    } catch {
      // Clipboard access can be denied by the platform; the command is
      // already visible in the banner for the user to copy by hand.
    }
  }

  $effect(() => {
    for (const item of $projects.items) {
      const failedJobId = item.job && item.job.status === "failed" ? item.job.id : null;
      if (failedJobId && !(failedJobId in errorDetails)) {
        loadErrorDetail(failedJobId);
      }
    }
  });

  function loadErrorDetail(jobId: string): void {
    errorDetails = { ...errorDetails, [jobId]: "" };
    api
      .getJob(jobId)
      .then((detail) => {
        errorDetails = { ...errorDetails, [jobId]: detail.error_detail ?? detail.error_code ?? "Transcription failed" };
      })
      .catch(() => {
        errorDetails = { ...errorDetails, [jobId]: "Transcription failed" };
      });
  }

  onDestroy(() => {
    projects.stopPolling();
  });

  function rowStatus(project: ProjectListItem): RowStatus {
    if (!project.job) return "none";
    if (project.job.status === "succeeded") return "succeeded";
    if (project.job.status === "failed") return "failed";
    return "running";
  }

  function instrumentLabel(instrument: string): string {
    return instrument.length === 0 ? instrument : instrument.charAt(0).toUpperCase() + instrument.slice(1);
  }

  function formatDuration(ms: number | null): string {
    if (ms === null) return "—";
    const totalSeconds = Math.round(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return `${minutes}:${seconds.toString().padStart(2, "0")}`;
  }

  function formatRelativeTime(iso: string): string {
    const thenMs = new Date(iso).getTime();
    const diffMs = Math.max(0, Date.now() - thenMs);
    const minute = 60_000;
    const hour = 60 * minute;
    const day = 24 * hour;
    if (diffMs < minute) return "just now";
    if (diffMs < hour) {
      const minutes = Math.floor(diffMs / minute);
      return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
    }
    if (diffMs < day) {
      const hours = Math.floor(diffMs / hour);
      return `${hours} hour${hours === 1 ? "" : "s"} ago`;
    }
    const days = Math.floor(diffMs / day);
    return `${days} day${days === 1 ? "" : "s"} ago`;
  }

  function factsLine(project: ProjectListItem): string {
    return [instrumentLabel(project.instrument), formatDuration(project.duration_ms), formatRelativeTime(project.created_at)].join(
      " · ",
    );
  }

  function navigate(id: string): void {
    window.location.hash = `#/project/${id}`;
  }

  function openFileBrowser(): void {
    fileInput?.click();
  }

  function isAcceptedFile(file: File): boolean {
    const name = file.name.toLowerCase();
    return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext)) || file.type.startsWith("audio/");
  }

  function handleFile(file: File): void {
    if (!isAcceptedFile(file)) {
      creationError = `"${file.name}" doesn't look like a WAV, MP3, or M4A file.`;
      return;
    }
    creationError = null;
    pendingFile = file;
  }

  function onFileInputChange(event: Event): void {
    const target = event.target as HTMLInputElement;
    const file = target.files?.[0];
    if (file) handleFile(file);
    target.value = "";
  }

  function onDragOver(event: DragEvent): void {
    if (pendingFile || creating) return;
    event.preventDefault();
    isDragOver = true;
  }

  function onDragLeave(): void {
    isDragOver = false;
  }

  function onDrop(event: DragEvent): void {
    event.preventDefault();
    isDragOver = false;
    if (pendingFile || creating) return;
    const file = event.dataTransfer?.files?.[0];
    if (file) handleFile(file);
  }

  function cancelPending(): void {
    pendingFile = null;
    creationError = null;
  }

  function titleFromFilename(name: string): string {
    const dot = name.lastIndexOf(".");
    return dot > 0 ? name.slice(0, dot) : name;
  }

  async function chooseInstrument(instrument: "guitar" | "piano"): Promise<void> {
    const file = pendingFile;
    if (!file || creating) return;
    creating = true;
    creationError = null;
    try {
      const uploaded = await api.upload(file);
      const project = await api.createProject(titleFromFilename(file.name), instrument, uploaded.object_key);
      await api.startTranscription(project.id);
      pendingFile = null;
      await projects.refresh();
    } catch (err: unknown) {
      creationError = err instanceof Error ? err.message : String(err);
    } finally {
      creating = false;
    }
  }

  async function retry(project: ProjectListItem): Promise<void> {
    retryingId = project.id;
    retryError = null;
    try {
      await api.startTranscription(project.id);
      await projects.refresh();
    } catch (err: unknown) {
      retryError = err instanceof Error ? err.message : String(err);
    } finally {
      retryingId = null;
    }
  }
</script>

{#snippet wordmark()}
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M2 12h2.5l1.5-6 3 16 3-12 2 6h8"
      stroke="var(--accent)"
      stroke-width="2"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
{/snippet}

{#snippet plusIcon()}
  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
  </svg>
{/snippet}

{#snippet uploadIcon()}
  <svg width="28" height="28" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <path
      d="M12 16V4M12 4l-4 4M12 4l4 4M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2"
      stroke="var(--dim)"
      stroke-width="1.8"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
  </svg>
{/snippet}

{#snippet guitarIcon()}
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <circle cx="9" cy="16" r="5" stroke="var(--accent)" stroke-width="1.6" />
    <path d="M11.5 12.5 19 5M17 3l4 4-2 2-4-4z" stroke="var(--accent)" stroke-width="1.6" stroke-linejoin="round" />
    <path d="M7.5 15h3M9 13.5v3" stroke="var(--accent)" stroke-width="1.2" stroke-linecap="round" />
  </svg>
{/snippet}

{#snippet pianoIcon()}
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    <rect x="3" y="7" width="18" height="11" rx="1.5" stroke="var(--accent)" stroke-width="1.6" />
    <path d="M7.5 7v7M12 7v7M16.5 7v7" stroke="var(--accent)" stroke-width="1.4" />
  </svg>
{/snippet}

<div class="page">
  <header class="topbar">
    <div class="brand">
      {@render wordmark()}
      <span class="brand-name">AuraAudio</span>
    </div>
    <button type="button" class="new-transcription" onclick={openFileBrowser} disabled={creating}>
      {@render plusIcon()}
      New transcription
    </button>
  </header>

  <input
    bind:this={fileInput}
    type="file"
    class="visually-hidden"
    accept="audio/wav,audio/x-wav,audio/mpeg,audio/mp4,.wav,.mp3,.m4a"
    onchange={onFileInputChange}
  />

  <main class="content">
    <!-- The drop target's real interactivity (keyboard + click) lives on the
         <button> inside it; this wrapper only adds drag-and-drop, which has
         no meaningful ARIA role of its own. -->
    <!-- svelte-ignore a11y_no_static_element_interactions -->
    <div
      class="dropzone"
      class:dragover={isDragOver}
      ondragover={onDragOver}
      ondragleave={onDragLeave}
      ondrop={onDrop}
    >
      {#if pendingFile}
        <div class="instrument-choice">
          <p class="filename">{pendingFile.name}</p>
          <p class="prompt">Which instrument is this?</p>
          <div class="choice-buttons">
            <button
              type="button"
              onclick={() => chooseInstrument("guitar")}
              disabled={creating || $deps.status === "missing"}
              title={$deps.status === "missing"
                ? `ffmpeg is required for transcription — install it below, then "Check again".`
                : undefined}
            >
              {@render guitarIcon()} Guitar
            </button>
            <button
              type="button"
              onclick={() => chooseInstrument("piano")}
              disabled={creating || $deps.status === "missing"}
              title={$deps.status === "missing"
                ? `ffmpeg is required for transcription — install it below, then "Check again".`
                : undefined}
            >
              {@render pianoIcon()} Piano
            </button>
          </div>
          <button type="button" class="cancel-link" onclick={cancelPending} disabled={creating}>
            {creating ? "Creating…" : "Cancel"}
          </button>
        </div>
      {:else}
        <button type="button" class="dropzone-trigger" onclick={openFileBrowser}>
          {@render uploadIcon()}
          <p class="prompt">Drop an audio file to transcribe</p>
          <p class="hint">WAV, MP3, or M4A — solo guitar or piano · or click to browse</p>
        </button>
      {/if}
    </div>

    {#if $deps.status === "missing" || ($deps.status === "checking" && $deps.detail !== null && !$deps.detail.allFound)}
      <div class="deps-banner" role="alert">
        <p class="deps-message">
          <strong>{missingBinaryNames()}</strong> not found on your system. ffmpeg is required to decode
          audio before transcription can run.
        </p>
        <div class="deps-command-row">
          <code class="deps-command">{installCommand}</code>
          <button type="button" class="deps-copy" onclick={copyInstallCommand}>
            {depsCopyFeedback ? "Copied" : "Copy"}
          </button>
        </div>
        <button
          type="button"
          class="deps-recheck"
          disabled={$deps.status === "checking"}
          onclick={() => deps.recheck()}
        >
          {$deps.status === "checking" ? "Checking…" : "Check again"}
        </button>
      </div>
    {/if}

    {#if creationError}
      <div class="error-panel">{creationError}</div>
    {/if}
    {#if retryError}
      <div class="error-panel">{retryError}</div>
    {/if}

    {#if $projects.error}
      <div class="error-panel">
        Couldn't load projects: {$projects.error}
        <button type="button" class="retry-link" onclick={() => projects.refresh()}>Retry</button>
      </div>
    {:else if $projects.items.length > 0}
      <h2 class="section-label">Recent projects</h2>
      <ul class="project-list">
        {#each $projects.items as project (project.id)}
          {@const status = rowStatus(project)}
          <li>
            <!-- role/tabindex are paired and only ever both present together
                 (status === "succeeded"); svelte-check can't see that the two
                 dynamic attributes are correlated. -->
            <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
            <div
              class="row"
              class:clickable={status === "succeeded"}
              role={status === "succeeded" ? "button" : undefined}
              tabindex={status === "succeeded" ? 0 : undefined}
              onclick={() => status === "succeeded" && navigate(project.id)}
              onkeydown={(e) => {
                if (status === "succeeded" && (e.key === "Enter" || e.key === " ")) {
                  e.preventDefault();
                  navigate(project.id);
                }
              }}
            >
              <div class="icon-tile">
                {#if project.instrument === "piano"}
                  {@render pianoIcon()}
                {:else}
                  {@render guitarIcon()}
                {/if}
              </div>
              <div class="row-main">
                <p class="row-title">{project.title}</p>
                <p class="row-facts">{factsLine(project)}</p>
              </div>
              <div class="row-status">
                {#if status === "succeeded"}
                  <span class="chip chip-success">Transcribed</span>
                {:else if status === "failed"}
                  <span
                    class="chip chip-error"
                    title={(project.job && errorDetails[project.job.id]) || "Transcription failed"}
                  >
                    Failed
                  </span>
                  <button
                    type="button"
                    class="retry-button"
                    disabled={retryingId === project.id}
                    onclick={(e) => {
                      e.stopPropagation();
                      void retry(project);
                    }}
                  >
                    {retryingId === project.id ? "Retrying…" : "Retry"}
                  </button>
                {:else if status === "running"}
                  <div class="progress-wrap">
                    <div class="progress-track">
                      <div class="progress-fill" style={`width:${project.job?.progress ?? 0}%`}></div>
                    </div>
                    <span class="progress-pct">{project.job?.progress ?? 0}%</span>
                  </div>
                  <span class="stage-label">{project.job?.stage ?? "queued"}</span>
                {:else}
                  <span class="chip chip-pending">Preparing…</span>
                {/if}
              </div>
            </div>
          </li>
        {/each}
      </ul>
    {/if}
  </main>
</div>

<style>
  .page {
    min-height: 100vh;
    background: var(--bg);
    color: var(--text);
  }

  .topbar {
    height: 64px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 clamp(20px, 6vw, 120px);
    border-bottom: 1px solid var(--border);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .brand-name {
    font-size: 16px;
    font-weight: 600;
    letter-spacing: 0.01em;
  }

  .new-transcription {
    display: flex;
    align-items: center;
    gap: 6px;
    background: var(--accent);
    color: #1e1d21;
    border: none;
    border-radius: 8px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: filter 0.15s ease;
  }

  .new-transcription:hover:not(:disabled) {
    filter: brightness(1.08);
  }

  .new-transcription:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  .content {
    max-width: 1040px;
    margin: 0 auto;
    padding: 32px clamp(20px, 6vw, 120px) 64px;
  }

  .dropzone {
    min-height: 180px;
    border: 1.5px dashed var(--border);
    border-radius: 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    transition: border-color 0.15s ease, background 0.15s ease;
    margin-bottom: 32px;
  }

  .dropzone.dragover {
    border-color: var(--accent);
    background: rgba(217, 154, 78, 0.06);
  }

  .dropzone-trigger {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 8px;
    background: none;
    border: none;
    color: inherit;
    cursor: pointer;
    padding: 24px;
    width: 100%;
  }

  .prompt {
    margin: 0;
    font-size: 14px;
    font-weight: 500;
    color: var(--text);
  }

  .hint {
    margin: 0;
    font-size: 12px;
    color: var(--dim);
  }

  .instrument-choice {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 20px;
  }

  .filename {
    margin: 0;
    font-size: 13px;
    color: var(--dim);
    max-width: 480px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .choice-buttons {
    display: flex;
    gap: 12px;
  }

  .choice-buttons button {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--panel);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 9px;
    padding: 10px 20px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: border-color 0.15s ease, background 0.15s ease;
  }

  .choice-buttons button:hover:not(:disabled) {
    border-color: var(--accent);
    background: var(--border);
  }

  .choice-buttons button:disabled {
    opacity: 0.5;
    cursor: default;
  }

  .cancel-link {
    background: none;
    border: none;
    color: var(--dim);
    font-size: 12px;
    cursor: pointer;
    text-decoration: underline;
    padding: 0;
  }

  .deps-banner {
    background: var(--panel);
    border: 1px solid var(--border);
    border-left: 3px solid var(--accent);
    border-radius: 9px;
    padding: 14px 16px;
    margin-bottom: 20px;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .deps-message {
    margin: 0;
    font-size: 13px;
    color: var(--text);
  }

  .deps-message strong {
    color: var(--accent);
  }

  .deps-command-row {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .deps-command {
    flex: 1;
    min-width: 0;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    color: var(--text);
    overflow-x: auto;
    white-space: pre;
  }

  .deps-copy,
  .deps-recheck {
    background: none;
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
    white-space: nowrap;
  }

  .deps-copy:hover:not(:disabled),
  .deps-recheck:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }

  .deps-recheck {
    align-self: flex-start;
  }

  .deps-recheck:disabled {
    opacity: 0.6;
    cursor: default;
  }

  .error-panel {
    background: rgba(224, 99, 99, 0.1);
    border: 1px solid rgba(224, 99, 99, 0.35);
    color: #e58a8a;
    border-radius: 9px;
    padding: 12px 16px;
    font-size: 13px;
    margin-bottom: 20px;
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

  .section-label {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dim);
    margin: 0 0 12px;
  }

  .project-list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  .row {
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 10px 12px;
    border-radius: 10px;
    transition: background 0.15s ease;
  }

  .row.clickable {
    cursor: pointer;
  }

  .row.clickable:hover,
  .row.clickable:focus-visible {
    background: var(--border);
    outline: none;
  }

  .icon-tile {
    flex: none;
    width: 40px;
    height: 40px;
    border-radius: 9px;
    background: var(--border);
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .row-main {
    flex: 1;
    min-width: 0;
  }

  .row-title {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .row-facts {
    margin: 2px 0 0;
    font-size: 12px;
    color: var(--dim);
  }

  .row-status {
    flex: none;
    display: flex;
    align-items: center;
    gap: 10px;
  }

  .chip {
    font-size: 11px;
    font-weight: 600;
    padding: 4px 10px;
    border-radius: 999px;
    white-space: nowrap;
  }

  .chip-success {
    background: rgba(127, 176, 105, 0.12);
    color: var(--success);
  }

  .chip-error {
    background: rgba(224, 99, 99, 0.12);
    color: #e58a8a;
  }

  .chip-pending {
    background: rgba(155, 150, 140, 0.12);
    color: var(--dim);
  }

  .retry-button {
    background: none;
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
  }

  .retry-button:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }

  .progress-wrap {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .progress-track {
    width: 90px;
    height: 6px;
    border-radius: 999px;
    background: var(--border);
    overflow: hidden;
  }

  .progress-fill {
    height: 100%;
    background: var(--accent);
    border-radius: 999px;
    transition: width 0.3s ease;
  }

  .progress-pct {
    font-size: 11px;
    color: var(--dim);
    width: 32px;
    text-align: right;
  }

  .stage-label {
    font-size: 11px;
    color: var(--dim);
    text-transform: capitalize;
  }
</style>
