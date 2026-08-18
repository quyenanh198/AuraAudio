<script lang="ts">
  import { api } from "../lib/api";
  import type { ProjectExportSummary, ScorePart } from "../lib/types";

  interface Props {
    collapsed: boolean;
    onToggleCollapse: () => void;
    projectTitle: string;
    part: ScorePart | null;
    tabAvailable: boolean;
    tabVisible: boolean;
    onTabVisibleChange: (visible: boolean) => void;
    zoomPercent: number;
    onZoomChange: (percent: number) => void;
    exports: ProjectExportSummary[];
  }

  let {
    collapsed,
    onToggleCollapse,
    projectTitle,
    part,
    tabAvailable,
    tabVisible,
    onTabVisibleChange,
    zoomPercent,
    onZoomChange,
    exports: exportItems,
  }: Props = $props();

  const EXPORT_FORMATS: { format: string; label: string; extension: string }[] = [
    { format: "musicxml", label: "MusicXML", extension: "musicxml" },
    { format: "midi", label: "MIDI", extension: "mid" },
  ];

  function exportFor(format: string): ProjectExportSummary | undefined {
    return exportItems.find((item) => item.format === format);
  }

  function sanitizedFilename(extension: string): string {
    const base = projectTitle.trim().replace(/[^a-zA-Z0-9 _-]/g, "").replace(/\s+/g, "-") || "score";
    return `${base}.${extension}`;
  }

  function instrumentLabel(instrument: string): string {
    return instrument.length === 0 ? instrument : instrument.charAt(0).toUpperCase() + instrument.slice(1);
  }

  function confidenceDots(value: number): number {
    return Math.max(0, Math.min(5, Math.round(value * 5)));
  }

  function confidenceLevel(value: number): "low" | "medium" | "high" {
    if (value < 0.4) return "low";
    if (value < 0.7) return "medium";
    return "high";
  }
</script>

{#snippet collapseIcon()}
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
    {#if collapsed}
      <path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
    {:else}
      <path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" />
    {/if}
  </svg>
{/snippet}

<aside class="sidebar" class:collapsed>
  <button
    type="button"
    class="collapse-toggle"
    onclick={onToggleCollapse}
    aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
    title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
  >
    {@render collapseIcon()}
  </button>

  {#if !collapsed}
    <div class="sidebar-content">
      <section class="section">
        <h2 class="section-title">Detection</h2>
        {#if part}
          <dl class="facts">
            <div class="fact">
              <dt>Instrument</dt>
              <dd>{instrumentLabel(part.instrument)}</dd>
            </div>
            <div class="fact">
              <dt>Key</dt>
              <dd>
                {part.key}
                <span class="confidence-dots" title={`${Math.round(part.confidence.key * 100)}% confidence`}>
                  {#each Array(5) as _, i}
                    <span
                      class="dot"
                      class:filled={i < confidenceDots(part.confidence.key)}
                      class:low={confidenceLevel(part.confidence.key) === "low"}
                      class:medium={confidenceLevel(part.confidence.key) === "medium"}
                      class:high={confidenceLevel(part.confidence.key) === "high"}
                    ></span>
                  {/each}
                </span>
              </dd>
            </div>
            <div class="fact">
              <dt>Tempo</dt>
              <dd>
                {Math.round(part.tempoBpm)} BPM
                <span class="confidence-dots" title={`${Math.round(part.confidence.tempo * 100)}% confidence`}>
                  {#each Array(5) as _, i}
                    <span
                      class="dot"
                      class:filled={i < confidenceDots(part.confidence.tempo)}
                      class:low={confidenceLevel(part.confidence.tempo) === "low"}
                      class:medium={confidenceLevel(part.confidence.tempo) === "medium"}
                      class:high={confidenceLevel(part.confidence.tempo) === "high"}
                    ></span>
                  {/each}
                </span>
              </dd>
            </div>
            <div class="fact">
              <dt>Meter</dt>
              <dd>
                {part.meter}
                <span class="confidence-dots" title={`${Math.round(part.confidence.meter * 100)}% confidence`}>
                  {#each Array(5) as _, i}
                    <span
                      class="dot"
                      class:filled={i < confidenceDots(part.confidence.meter)}
                      class:low={confidenceLevel(part.confidence.meter) === "low"}
                      class:medium={confidenceLevel(part.confidence.meter) === "medium"}
                      class:high={confidenceLevel(part.confidence.meter) === "high"}
                    ></span>
                  {/each}
                </span>
              </dd>
            </div>
          </dl>
        {:else}
          <p class="empty-note">No score data.</p>
        {/if}
      </section>

      <section class="section">
        <h2 class="section-title">View</h2>
        <div class="view-row">
          <span class="view-label">Tab staff</span>
          <button
            type="button"
            class="toggle"
            class:on={tabVisible}
            disabled={!tabAvailable}
            aria-pressed={tabVisible}
            title={tabAvailable ? "Show or hide the TAB staff" : "This score has no TAB staff"}
            onclick={() => onTabVisibleChange(!tabVisible)}
          >
            <span class="toggle-knob"></span>
          </button>
        </div>
        <div class="view-row">
          <span class="view-label">Zoom</span>
          <div class="zoom-controls">
            <button type="button" class="zoom-button" onclick={() => onZoomChange(zoomPercent - 10)} aria-label="Zoom out">
              &minus;
            </button>
            <span class="zoom-pct">{zoomPercent}%</span>
            <button type="button" class="zoom-button" onclick={() => onZoomChange(zoomPercent + 10)} aria-label="Zoom in">
              +
            </button>
          </div>
        </div>
      </section>

      <section class="section">
        <h2 class="section-title">Export</h2>
        <div class="export-list">
          {#each EXPORT_FORMATS as { format, label, extension } (format)}
            {@const item = exportFor(format)}
            <a
              class="export-button"
              class:disabled={!item}
              href={item ? api.exportDownloadUrl(item.id) : undefined}
              download={item ? sanitizedFilename(extension) : undefined}
              aria-disabled={!item}
              tabindex={item ? 0 : -1}
              onclick={(event) => {
                if (!item) event.preventDefault();
              }}
            >
              Export {label}
            </a>
          {/each}
        </div>
      </section>
    </div>
  {/if}
</aside>

<style>
  .sidebar {
    width: 260px;
    flex: none;
    background: var(--panel);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    transition: width 0.15s ease;
    overflow: hidden;
  }

  .sidebar.collapsed {
    width: 44px;
  }

  .collapse-toggle {
    display: flex;
    align-items: center;
    justify-content: center;
    width: 100%;
    height: 40px;
    background: none;
    border: none;
    border-bottom: 1px solid var(--border);
    color: var(--dim);
    cursor: pointer;
    flex: none;
  }

  .collapse-toggle:hover {
    color: var(--text);
  }

  .sidebar-content {
    overflow-y: auto;
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 24px;
  }

  .section-title {
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--dim);
    margin: 0 0 12px;
  }

  .facts {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .fact {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 8px;
  }

  .fact dt {
    font-size: 12px;
    color: var(--dim);
  }

  .fact dd {
    margin: 0;
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .confidence-dots {
    display: inline-flex;
    gap: 2px;
  }

  .dot {
    width: 5px;
    height: 5px;
    border-radius: 50%;
    background: var(--border);
  }

  .dot.filled.low {
    background: #e0836a;
  }

  .dot.filled.medium {
    background: var(--accent);
  }

  .dot.filled.high {
    background: var(--success);
  }

  .empty-note {
    margin: 0;
    font-size: 12px;
    color: var(--dim);
  }

  .view-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 0;
  }

  .view-label {
    font-size: 13px;
    color: var(--text);
  }

  .toggle {
    width: 34px;
    height: 20px;
    border-radius: 999px;
    background: var(--border);
    border: none;
    padding: 2px;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: flex-start;
    transition: background 0.15s ease;
  }

  .toggle.on {
    background: var(--accent);
    justify-content: flex-end;
  }

  .toggle:disabled {
    opacity: 0.35;
    cursor: default;
  }

  .toggle-knob {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--text);
    display: block;
  }

  .zoom-controls {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .zoom-button {
    width: 22px;
    height: 22px;
    border-radius: 6px;
    border: 1px solid var(--border);
    background: none;
    color: var(--text);
    font-size: 14px;
    line-height: 1;
    cursor: pointer;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  .zoom-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .zoom-pct {
    font-size: 12px;
    color: var(--dim);
    width: 38px;
    text-align: center;
  }

  .export-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .export-button {
    display: block;
    text-align: center;
    background: none;
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    text-decoration: none;
    transition: border-color 0.15s ease, color 0.15s ease;
  }

  .export-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .export-button.disabled {
    opacity: 0.4;
    cursor: default;
    pointer-events: none;
  }
</style>
