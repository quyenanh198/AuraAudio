<script lang="ts">
  import { onDestroy } from "svelte";

  import { api } from "../lib/api";
  import { editor } from "../lib/editor";
  import {
    NOTE_NAME_OPTIONS,
    clampPitch,
    findEvent,
    nameOctaveToPitch,
    pitchToName,
    stepDuration,
    stepOnset,
  } from "../lib/noteEdit";
  import type { EditOp, ProjectExportSummary, ScoreEvent, ScorePart } from "../lib/types";

  interface Props {
    collapsed: boolean;
    onToggleCollapse: () => void;
    projectTitle: string;
    projectId: string;
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
    projectId,
    part,
    tabAvailable,
    tabVisible,
    onTabVisibleChange,
    zoomPercent,
    onZoomChange,
    exports: exportItems,
  }: Props = $props();

  // --- Shared apply plumbing ---------------------------------------------
  //
  // `lastEditField` tags which control most recently issued an edit, so a
  // 422's message (`editor.error`) can be rendered under THAT control
  // specifically rather than as one generic sidebar-wide message — the brief
  // asks for inline, per-control errors. `editor.error` itself is a single
  // global string (see editor.ts), so this tag is what makes "under the
  // offending control" possible: it's set right before every apply() call
  // and stays put until the next one, matching how long `editor.error`
  // itself stays set (runOp clears it at the START of the next call, not
  // when a component re-renders).
  let lastEditField = $state<string | null>(null);

  function applyOp(field: string, op: EditOp): void {
    lastEditField = field;
    void editor.apply(projectId, op);
  }

  function fieldError(field: string): string | null {
    return lastEditField === field ? $editor.error : null;
  }

  // --- Inspector: selected event ------------------------------------------

  let selectedEntry = $derived(findEvent($editor.score, $editor.selectedEventId));
  let selectedEvent = $derived<ScoreEvent | null>(selectedEntry?.event ?? null);

  function stepPitch(direction: 1 | -1, semitones: number): void {
    if (!selectedEvent) return;
    applyOp("pitch", {
      type: "set_pitch",
      eventId: selectedEvent.id,
      pitch: clampPitch(selectedEvent.pitch + direction * semitones),
    });
  }

  function stepOnsetControl(direction: 1 | -1): void {
    if (!selectedEvent || !part) return;
    applyOp("onset", {
      type: "move_note",
      eventId: selectedEvent.id,
      notatedOnset: stepOnset(selectedEvent.notatedOnset, direction, part.meter),
    });
  }

  function stepDurationControl(direction: 1 | -1): void {
    if (!selectedEvent) return;
    applyOp("duration", {
      type: "set_duration",
      eventId: selectedEvent.id,
      notatedDuration: stepDuration(selectedEvent.notatedDuration, direction),
    });
  }

  function toggleLock(): void {
    if (!selectedEvent) return;
    applyOp("lock", { type: "set_locked", eventId: selectedEvent.id, locked: !selectedEvent.locked });
  }

  function deleteSelected(): void {
    if (!selectedEvent) return;
    applyOp("delete", { type: "delete_note", eventId: selectedEvent.id });
    editor.clearSelection();
  }

  // Fingering (guitar): string 0-5, fret 0-20 — both required by
  // SetFingeringOp, so local editable copies are kept and sent together on
  // change rather than one field at a time.
  let fingeringString = $state(0);
  let fingeringFret = $state(0);

  $effect(() => {
    fingeringString = selectedEvent?.string ?? 0;
    fingeringFret = selectedEvent?.fret ?? 0;
  });

  function commitFingering(): void {
    if (!selectedEvent) return;
    if (!Number.isInteger(fingeringString) || fingeringString < 0 || fingeringString > 5) {
      lastEditField = "fingering";
      fingeringClientError = "String must be an integer 0-5.";
      return;
    }
    if (!Number.isInteger(fingeringFret) || fingeringFret < 0 || fingeringFret > 20) {
      lastEditField = "fingering";
      fingeringClientError = "Fret must be an integer 0-20.";
      return;
    }
    fingeringClientError = null;
    applyOp("fingering", {
      type: "set_fingering",
      eventId: selectedEvent.id,
      string: fingeringString,
      fret: fingeringFret,
    });
  }

  let fingeringClientError = $state<string | null>(null);

  function setHand(hand: "left" | "right"): void {
    if (!selectedEvent) return;
    applyOp("hand", { type: "set_hand", eventId: selectedEvent.id, hand });
  }

  // --- Add-note mini-form --------------------------------------------------

  const DURATION_OPTIONS: { value: string; label: string }[] = [
    { value: "1/16", label: "16th" },
    { value: "1/8", label: "8th" },
    { value: "1/4", label: "Quarter" },
    { value: "1/2", label: "Half" },
    { value: "1/1", label: "Whole" },
  ];

  let addNoteName = $state("C");
  let addNoteOctave = $state(4);
  let addNoteDuration = $state("1/4");

  function addNoteTarget(): { measureNumber: number; notatedOnset: string } {
    if (selectedEntry) {
      return { measureNumber: selectedEntry.measureNumber, notatedOnset: selectedEntry.event.notatedOnset };
    }
    return { measureNumber: 1, notatedOnset: "0/1" };
  }

  function handleAddNote(): void {
    const { measureNumber, notatedOnset } = addNoteTarget();
    applyOp("add-note", {
      type: "add_note",
      measureNumber,
      notatedOnset,
      notatedDuration: addNoteDuration,
      pitch: nameOctaveToPitch(addNoteName, addNoteOctave),
    });
  }

  // --- Editable facts -------------------------------------------------------

  // The score_schema backend whitelist (packages/score_schema/src/
  // score_schema/edits.py::_ALLOWED_METERS) only accepts "4/4" and "3/4" —
  // deliberately narrower than an earlier draft of this UI that also
  // offered "6/8" (that value would 422 on every attempt), so only the two
  // backend-accepted meters are offered here.
  const METER_OPTIONS = ["4/4", "3/4"];

  const KEY_TONICS = ["A", "A#", "B", "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#"];
  const KEY_MODES = ["major", "minor"];
  const KEY_OPTIONS = KEY_TONICS.flatMap((tonic) => KEY_MODES.map((mode) => `${tonic} ${mode}`));

  // score_schema's key pattern (`^[A-G](#|-)? (major|minor)$`) also accepts
  // "-" for flats — if the detected key used that spelling it wouldn't be
  // one of KEY_OPTIONS above; folding it in here keeps the <select> from
  // silently jumping to a different value the instant it's opened.
  let keyOptions = $derived(part && !KEY_OPTIONS.includes(part.key) ? [part.key, ...KEY_OPTIONS] : KEY_OPTIONS);

  function handleKeyChange(value: string): void {
    applyOp("key", { type: "set_part_fact", field: "key", value });
  }

  function handleMeterChange(value: string): void {
    applyOp("meter", { type: "set_part_fact", field: "meter", value });
  }

  let tempoInput = $state("");
  let tempoClientError = $state<string | null>(null);

  $effect(() => {
    tempoInput = part ? String(Math.round(part.tempoBpm)) : "";
  });

  function commitTempo(): void {
    const value = Number(tempoInput);
    if (!Number.isFinite(value) || value < 20 || value > 300) {
      lastEditField = "tempo";
      tempoClientError = "Tempo must be a number between 20 and 300 BPM.";
      return;
    }
    tempoClientError = null;
    applyOp("tempo", { type: "set_part_fact", field: "tempoBpm", value });
  }

  // --- Undo / redo / revert -------------------------------------------------

  function handleUndo(): void {
    void editor.undo(projectId);
  }

  function handleRedo(): void {
    void editor.redo(projectId);
  }

  const REVERT_CONFIRM_MS = 3000;
  let revertConfirming = $state(false);
  let revertTimeout: ReturnType<typeof setTimeout> | null = null;

  function handleRevertClick(): void {
    if (!revertConfirming) {
      revertConfirming = true;
      revertTimeout = setTimeout(() => {
        revertConfirming = false;
        revertTimeout = null;
      }, REVERT_CONFIRM_MS);
      return;
    }
    if (revertTimeout) clearTimeout(revertTimeout);
    revertTimeout = null;
    revertConfirming = false;
    void editor.revert(projectId);
  }

  onDestroy(() => {
    if (revertTimeout) clearTimeout(revertTimeout);
  });

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
            <div class="fact fact-editable">
              <dt>Key</dt>
              <dd>
                <select class="fact-select" value={part.key} onchange={(e) => handleKeyChange((e.currentTarget as HTMLSelectElement).value)}>
                  {#each keyOptions as option (option)}
                    <option value={option}>{option}</option>
                  {/each}
                </select>
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
                {#if fieldError("key")}
                  <p class="field-error">{fieldError("key")}</p>
                {/if}
              </dd>
            </div>
            <div class="fact fact-editable">
              <dt>Tempo</dt>
              <dd>
                <input
                  class="fact-input"
                  type="number"
                  min="20"
                  max="300"
                  value={tempoInput}
                  oninput={(e) => (tempoInput = (e.currentTarget as HTMLInputElement).value)}
                  onchange={commitTempo}
                />
                <span class="unit">BPM</span>
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
                {#if tempoClientError || fieldError("tempo")}
                  <p class="field-error">{tempoClientError ?? fieldError("tempo")}</p>
                {/if}
              </dd>
            </div>
            <div class="fact fact-editable">
              <dt>Meter</dt>
              <dd>
                <select class="fact-select" value={part.meter} onchange={(e) => handleMeterChange((e.currentTarget as HTMLSelectElement).value)}>
                  {#each METER_OPTIONS as option (option)}
                    <option value={option}>{option}</option>
                  {/each}
                </select>
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
                {#if fieldError("meter")}
                  <p class="field-error">{fieldError("meter")}</p>
                {/if}
              </dd>
            </div>
          </dl>
        {:else}
          <p class="empty-note">No score data.</p>
        {/if}
      </section>

      <section class="section">
        <h2 class="section-title">History</h2>
        <div class="history-row">
          <button type="button" class="history-button" disabled={!$editor.canUndo} onclick={handleUndo}>Undo</button>
          <button type="button" class="history-button" disabled={!$editor.canRedo} onclick={handleRedo}>Redo</button>
          <button type="button" class="history-button revert" onclick={handleRevertClick}>
            {revertConfirming ? "Confirm revert?" : "Revert"}
          </button>
        </div>
      </section>

      {#if selectedEvent}
        <section class="section">
          <h2 class="section-title">Inspector</h2>
          <div class="inspector">
            <div class="inspector-row">
              <span class="inspector-label">Pitch</span>
              <div class="stepper">
                <button type="button" class="stepper-button" onclick={() => stepPitch(-1, 1)} aria-label="Pitch down a semitone">&minus;</button>
                <span class="stepper-value">{pitchToName(selectedEvent.pitch)}</span>
                <button type="button" class="stepper-button" onclick={() => stepPitch(1, 1)} aria-label="Pitch up a semitone">+</button>
              </div>
            </div>
            {#if fieldError("pitch")}
              <p class="field-error">{fieldError("pitch")}</p>
            {/if}

            <div class="inspector-row">
              <span class="inspector-label">Onset</span>
              <div class="stepper">
                <button type="button" class="stepper-button" onclick={() => stepOnsetControl(-1)} aria-label="Move earlier">&minus;</button>
                <span class="stepper-value">{selectedEvent.notatedOnset}</span>
                <button type="button" class="stepper-button" onclick={() => stepOnsetControl(1)} aria-label="Move later">+</button>
              </div>
            </div>
            {#if fieldError("onset")}
              <p class="field-error">{fieldError("onset")}</p>
            {/if}

            <div class="inspector-row">
              <span class="inspector-label">Duration</span>
              <div class="stepper">
                <button type="button" class="stepper-button" onclick={() => stepDurationControl(-1)} aria-label="Shorter">&minus;</button>
                <span class="stepper-value">{selectedEvent.notatedDuration}</span>
                <button type="button" class="stepper-button" onclick={() => stepDurationControl(1)} aria-label="Longer">+</button>
              </div>
            </div>
            {#if fieldError("duration")}
              <p class="field-error">{fieldError("duration")}</p>
            {/if}

            <div class="inspector-row">
              <span class="inspector-label">Voice</span>
              <span class="inspector-readonly">{selectedEvent.voice}</span>
            </div>

            {#if part?.instrument === "guitar"}
              <div class="inspector-row">
                <span class="inspector-label">String / Fret</span>
                <div class="fingering-inputs">
                  <input
                    class="fact-input small"
                    type="number"
                    min="0"
                    max="5"
                    bind:value={fingeringString}
                    onchange={commitFingering}
                    aria-label="String"
                  />
                  <input
                    class="fact-input small"
                    type="number"
                    min="0"
                    max="20"
                    bind:value={fingeringFret}
                    onchange={commitFingering}
                    aria-label="Fret"
                  />
                </div>
              </div>
              {#if fingeringClientError || fieldError("fingering")}
                <p class="field-error">{fingeringClientError ?? fieldError("fingering")}</p>
              {/if}
            {:else if part?.instrument === "piano"}
              <div class="inspector-row">
                <span class="inspector-label">Hand</span>
                <div class="hand-toggle" role="group" aria-label="Hand">
                  <button type="button" class="hand-button" class:active={selectedEvent.hand === "left"} onclick={() => setHand("left")}>Left</button>
                  <button type="button" class="hand-button" class:active={selectedEvent.hand === "right"} onclick={() => setHand("right")}>Right</button>
                </div>
              </div>
              {#if fieldError("hand")}
                <p class="field-error">{fieldError("hand")}</p>
              {/if}
            {/if}

            <div class="inspector-row">
              <span class="inspector-label">Locked</span>
              <button
                type="button"
                class="toggle"
                class:on={selectedEvent.locked}
                aria-pressed={selectedEvent.locked}
                aria-label={selectedEvent.locked ? "Unlock note" : "Lock note"}
                title={selectedEvent.locked ? "Unlock note" : "Lock note"}
                onclick={toggleLock}
              >
                <span class="toggle-knob"></span>
              </button>
            </div>

            <div class="inspector-row">
              <span class="inspector-label">Confidence</span>
              <span class="inspector-readonly">{Math.round(selectedEvent.confidence * 100)}%</span>
            </div>

            <button type="button" class="delete-button" onclick={deleteSelected}>Delete note</button>
          </div>
        </section>
      {/if}

      <section class="section">
        <h2 class="section-title">Add note</h2>
        <div class="add-note-form">
          <select class="fact-select" bind:value={addNoteName} aria-label="Pitch name">
            {#each NOTE_NAME_OPTIONS as name (name)}
              <option value={name}>{name}</option>
            {/each}
          </select>
          <select class="fact-select" bind:value={addNoteOctave} aria-label="Octave">
            {#each Array(9) as _, octave (octave)}
              <option value={octave}>{octave}</option>
            {/each}
          </select>
          <select class="fact-select" bind:value={addNoteDuration} aria-label="Duration">
            {#each DURATION_OPTIONS as { value, label } (value)}
              <option value={value}>{label}</option>
            {/each}
          </select>
          <button type="button" class="add-note-button" onclick={handleAddNote}>
            Add at {selectedEvent ? "selection" : "measure 1, beat 0"}
          </button>
        </div>
        {#if fieldError("add-note")}
          <p class="field-error">{fieldError("add-note")}</p>
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

  /* --- Editable facts (Task 7) ------------------------------------------ */

  .fact-editable dd {
    flex-wrap: wrap;
  }

  .fact-select,
  .fact-input {
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 3px 6px;
    font-size: 12px;
    font-family: inherit;
  }

  .fact-input {
    width: 64px;
  }

  .fact-input.small {
    width: 44px;
  }

  .fact-select:focus,
  .fact-input:focus {
    outline: none;
    border-color: var(--accent);
  }

  .unit {
    font-size: 11px;
    color: var(--dim);
  }

  .field-error {
    flex-basis: 100%;
    margin: 2px 0 0;
    font-size: 11px;
    color: #e0836a;
  }

  /* --- History (Task 7) -------------------------------------------------- */

  .history-row {
    display: flex;
    gap: 8px;
  }

  .history-button {
    flex: 1;
    background: none;
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 6px;
    padding: 6px 8px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .history-button:hover:not(:disabled) {
    border-color: var(--accent);
    color: var(--accent);
  }

  .history-button:disabled {
    opacity: 0.35;
    cursor: default;
  }

  .history-button.revert {
    border-color: rgba(224, 99, 99, 0.4);
    color: #e0836a;
  }

  .history-button.revert:hover {
    border-color: #e0836a;
  }

  /* --- Inspector (Task 7) ------------------------------------------------- */

  .inspector {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  .inspector-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 4px 0;
  }

  .inspector-label {
    font-size: 12px;
    color: var(--dim);
  }

  .inspector-readonly {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
  }

  .stepper {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .stepper-button {
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

  .stepper-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .stepper-value {
    font-size: 13px;
    font-weight: 600;
    color: var(--text);
    min-width: 44px;
    text-align: center;
    font-variant-numeric: tabular-nums;
  }

  .fingering-inputs {
    display: flex;
    gap: 6px;
  }

  .hand-toggle {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
  }

  .hand-button {
    background: transparent;
    border: none;
    color: var(--dim);
    padding: 4px 10px;
    font-size: 12px;
    cursor: pointer;
  }

  .hand-button:not(:first-child) {
    border-left: 1px solid var(--border);
  }

  .hand-button.active {
    background: var(--accent);
    color: #1e1d21;
  }

  .delete-button {
    margin-top: 6px;
    background: none;
    border: 1px solid rgba(224, 99, 99, 0.4);
    color: #e0836a;
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }

  .delete-button:hover {
    border-color: #e0836a;
    background: rgba(224, 99, 99, 0.1);
  }

  /* --- Add note (Task 7) --------------------------------------------------- */

  .add-note-form {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }

  .add-note-button {
    flex-basis: 100%;
    background: none;
    border: 1px solid var(--border);
    color: var(--text);
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }

  .add-note-button:hover {
    border-color: var(--accent);
    color: var(--accent);
  }
</style>
