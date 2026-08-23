<script lang="ts">
  import { onDestroy } from "svelte";

  import { api } from "../lib/api";
  import { editor } from "../lib/editor";
  import {
    METER_OPTIONS,
    NOTE_NAME_OPTIONS,
    clampPitch,
    findEvent,
    formatKeyForDisplay,
    nameOctaveToPitch,
    pitchToName,
    stepDuration,
    stepOnset,
    validateMeasureNumber,
  } from "../lib/noteEdit";
  import type { PdfPageSize } from "../lib/exportPdf";
  import { isTauri, saveExport, savePdfBytes } from "../lib/saveExport";
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

  // Measure target: a plain-text field so an out-of-range/non-integer value
  // can be held and reported inline (matching the tempo/fingering
  // client-error pattern) instead of silently clamping. Defaults to the
  // selected note's measure (or 1) whenever the selection changes, letting
  // the user then type any measure — including a silent one with no
  // selectable note — to add into.
  let addNoteMeasureInput = $state("1");
  let addNoteMeasureClientError = $state<string | null>(null);

  $effect(() => {
    addNoteMeasureInput = String(selectedEntry?.measureNumber ?? 1);
  });

  // Measures are numbered contiguously 1..max by construction (see
  // score_schema/edits.py::_rebucket's invariant), so the part's measure
  // count IS the max valid measure number. Falls back to 1 when there's no
  // score yet, matching addNoteMeasureInput's own default.
  let maxMeasureNumber = $derived(part && part.measures.length > 0 ? part.measures.length : 1);

  function handleAddNote(): void {
    const validation = validateMeasureNumber(addNoteMeasureInput, maxMeasureNumber);
    if (!validation.ok) {
      lastEditField = "add-note";
      addNoteMeasureClientError = validation.error;
      return;
    }
    addNoteMeasureClientError = null;
    const { measureNumber } = validation;
    // Reuse the selected note's onset only when adding into that SAME
    // measure — a different (e.g. silent) target measure has no meaningful
    // relation to the selected note's onset, so it starts at beat 0.
    const notatedOnset =
      selectedEntry && selectedEntry.measureNumber === measureNumber ? selectedEntry.event.notatedOnset : "0/1";
    applyOp("add-note", {
      type: "add_note",
      measureNumber,
      notatedOnset,
      notatedDuration: addNoteDuration,
      pitch: nameOctaveToPitch(addNoteName, addNoteOctave),
    });
  }

  // --- Editable facts -------------------------------------------------------

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
    for (const timeout of exportSavedTimeouts.values()) clearTimeout(timeout);
    exportSavedTimeouts.clear();
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

  // --- Export: native Save dialog (Tauri) ------------------------------
  //
  // Outside Tauri the anchor's own `href`/`download` attributes still do
  // the work (plain-browser download) — the click handler below only
  // intercepts the click when running inside Tauri, where a bare
  // `<a download>` would otherwise land the file in the packaged app's
  // process cwd with no way for the user to find it.
  //
  // `EXPORT_STATUS_MS` must match the "export-status-fade" CSS animation's
  // duration below — it's how long the transient "Saved" confirmation
  // stays in the DOM before this state clears it.
  const EXPORT_STATUS_MS = 2000;
  let exportSavedFormat = $state<Record<string, boolean>>({});
  let exportErrorByFormat = $state<Record<string, string | null>>({});
  const exportSavedTimeouts = new Map<string, ReturnType<typeof setTimeout>>();

  /** "pdf" isn't in EXPORT_FORMATS/exportItems (it has no server-side
   * Export row — it's generated client-side on click, see
   * handleExportPdfClick below), so its busy/error/saved state can't
   * reuse `exportFor()`. `pdfExporting` gates the button specifically
   * (a multi-page render can take a second or two — the brief calls this
   * out explicitly) since exportErrorByFormat/exportSavedFormat's "pdf"
   * key already covers error/saved feedback the same way the other two
   * formats do. */
  let pdfExporting = $state(false);

  // --- PDF page size (A4 / Letter) --------------------------------------
  //
  // Client-side-only preference (nothing server-side depends on it, unlike
  // the two `<a download>` exports above), so it's read/written straight
  // to localStorage rather than plumbed through the project/editor state --
  // no existing preference-persistence pattern to follow elsewhere in this
  // codebase (checked: no other `localStorage` use in src/). This is the
  // Tauri desktop webview (real localStorage, not a sandboxed third-party
  // iframe), but reads/writes are still wrapped in try/catch: a private
  // window, disabled site data, or a first render before any prior choice
  // was ever saved should all just fall back to the default rather than
  // throw and break the Export section.
  const PDF_PAGE_SIZE_STORAGE_KEY = "auraaudio.pdfPageSize";
  const PDF_PAGE_SIZE_OPTIONS: { value: PdfPageSize; label: string }[] = [
    { value: "A4", label: "A4" },
    { value: "Letter", label: "Letter" },
  ];

  function loadStoredPdfPageSize(): PdfPageSize {
    try {
      const stored = localStorage.getItem(PDF_PAGE_SIZE_STORAGE_KEY);
      if (stored === "A4" || stored === "Letter") return stored;
    } catch {
      // Ignore -- fall back to the default below.
    }
    return "A4";
  }

  let pdfPageSize = $state<PdfPageSize>(loadStoredPdfPageSize());

  function handlePdfPageSizeChange(value: PdfPageSize): void {
    pdfPageSize = value;
    try {
      localStorage.setItem(PDF_PAGE_SIZE_STORAGE_KEY, value);
    } catch {
      // Ignore -- the picker still reflects the choice for this session
      // even if it can't be persisted for the next one.
    }
  }

  function showExportSaved(format: string): void {
    exportSavedFormat = { ...exportSavedFormat, [format]: true };
    const pending = exportSavedTimeouts.get(format);
    if (pending) clearTimeout(pending);
    exportSavedTimeouts.set(
      format,
      setTimeout(() => {
        exportSavedFormat = { ...exportSavedFormat, [format]: false };
        exportSavedTimeouts.delete(format);
      }, EXPORT_STATUS_MS),
    );
  }

  async function handleExportClick(
    event: MouseEvent,
    format: string,
    extension: string,
    item: ProjectExportSummary | undefined,
  ): Promise<void> {
    if (!item) {
      event.preventDefault();
      return;
    }
    if (!isTauri()) {
      // Plain browser: keep the anchor's native <a download> behavior.
      return;
    }

    event.preventDefault();
    exportErrorByFormat = { ...exportErrorByFormat, [format]: null };
    try {
      const result = await saveExport(api.exportDownloadUrl(item.id), sanitizedFilename(extension));
      if (result === "saved") {
        showExportSaved(format);
      }
      // "cancelled": the user dismissed the native dialog — no-op by design.
    } catch (err) {
      exportErrorByFormat = {
        ...exportErrorByFormat,
        [format]: err instanceof Error ? err.message : "Export failed.",
      };
    }
  }

  /** Export PDF: unlike the two `<a download>`-backed exports above, there
   * is no server-side Export row to link to — the PDF is generated
   * entirely client-side (offscreen OSMD render -> jsPDF/svg2pdf.js, see
   * lib/exportPdf.ts) from the SAME MusicXML export the notation view
   * itself loads. Reuses that export's id + the identical
   * `cache: "no-store"` fetch ScoreView.svelte's loadScore()/
   * refreshAfterEdit() use (SESSION-HANDOFF.md's OSMD gotcha #2: the
   * MusicXML download URL is stable across a rederive, only its BYTES
   * change, so the browser's heuristic HTTP cache must be bypassed or a
   * PDF exported right after an edit could render the pre-edit score).
   *
   * `savePdfBytes` handles both the Tauri native-Save-dialog path and the
   * plain-browser Blob-download fallback itself (there's no `<a href>`
   * for the click handler to fall back to here) — both "saved" and
   * "fallback" mean a real download happened, so both show the same
   * transient confirmation; only "cancelled" (the user dismissed the
   * native dialog) is a no-op, matching the other two exports' contract.
   *
   * Page size: `pdfPageSize` (the segmented control next to this button,
   * persisted to localStorage under PDF_PAGE_SIZE_STORAGE_KEY) is passed
   * straight through to `exportScoreToPdf` — it drives both OSMD's own
   * page layout and the PDF page dimensions, see exportPdf.ts.
   *
   * Title: `projectTitle` (this component's own prop — no extra fetch
   * needed) is passed straight through too. Bug 1 fix: the title is no
   * longer drawn by OSMD/svg2pdf.js at all (jsPDF's standard fonts are
   * WinAnsi-only and garbled non-Latin titles) — exportScoreToPdf renders
   * it itself as a Unicode-correct raster strip on page 1. See
   * exportPdf.ts's buildTitleImage().
   */
  async function handleExportPdfClick(): Promise<void> {
    if (pdfExporting) return;
    const musicxmlExport = exportFor("musicxml");
    if (!musicxmlExport) return;

    pdfExporting = true;
    exportErrorByFormat = { ...exportErrorByFormat, pdf: null };
    try {
      const xmlResp = await fetch(api.exportDownloadUrl(musicxmlExport.id), { cache: "no-store" });
      // Status + statusText only — matches saveExport.ts's own convention
      // for this exact class of error, not the raw response body (which
      // could be an arbitrarily large HTML/JSON error page from a proxy
      // or the dev server, not something fit for a one-line inline
      // field-error message).
      if (!xmlResp.ok) throw new Error(`MusicXML fetch failed: ${xmlResp.status} ${xmlResp.statusText}`);
      const xmlText = await xmlResp.text();

      // Dynamic import: jsPDF + svg2pdf.js (and their small deps --
      // cssesc, font-family-papandreou, svgpath, specificity) are a
      // non-trivial chunk of bytes that only this one click path ever
      // needs. A static top-level import would inline them into the
      // app's main bundle for every user on every load; deferring the
      // import to here keeps them out of it entirely until someone
      // actually clicks "Export PDF" (per rules/web/performance.md:
      // "Dynamically import heavy libraries").
      const { exportScoreToPdf } = await import("../lib/exportPdf");
      const bytes = await exportScoreToPdf(xmlText, pdfPageSize, projectTitle);
      const result = await savePdfBytes(bytes, sanitizedFilename("pdf"));
      if (result === "saved" || result === "fallback") {
        showExportSaved("pdf");
      }
      // "cancelled": the user dismissed the native dialog — no-op by design.
    } catch (err) {
      exportErrorByFormat = {
        ...exportErrorByFormat,
        pdf: err instanceof Error ? err.message : "PDF export failed.",
      };
    } finally {
      pdfExporting = false;
    }
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
                <select class="fact-select" value={part.key} onchange={(e) => handleKeyChange((e.currentTarget as HTMLSelectElement).value)} aria-label="Key">
                  {#each keyOptions as option (option)}
                    <option value={option}>{formatKeyForDisplay(option)}</option>
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
                  aria-label="Tempo"
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
                <select class="fact-select" value={part.meter} onchange={(e) => handleMeterChange((e.currentTarget as HTMLSelectElement).value)} aria-label="Meter">
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
          <input
            class="fact-input small"
            type="number"
            min="1"
            max={maxMeasureNumber}
            step="1"
            value={addNoteMeasureInput}
            oninput={(e) => (addNoteMeasureInput = (e.currentTarget as HTMLInputElement).value)}
            aria-label="Measure"
            title={`Measure (1-${maxMeasureNumber})`}
          />
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
          <button type="button" class="add-note-button" onclick={handleAddNote}> Add note </button>
        </div>
        {#if addNoteMeasureClientError || fieldError("add-note")}
          <p class="field-error">{addNoteMeasureClientError ?? fieldError("add-note")}</p>
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
            <div class="export-row">
              <a
                class="export-button"
                class:disabled={!item}
                href={item ? api.exportDownloadUrl(item.id) : undefined}
                download={item ? sanitizedFilename(extension) : undefined}
                aria-disabled={!item}
                tabindex={item ? 0 : -1}
                onclick={(event) => handleExportClick(event, format, extension, item)}
              >
                Export {label}
              </a>
              {#if exportSavedFormat[format]}
                <span class="export-status">Saved</span>
              {/if}
              {#if exportErrorByFormat[format]}
                <p class="field-error">{exportErrorByFormat[format]}</p>
              {/if}
            </div>
          {/each}
          <div class="export-row">
            <div class="pdf-page-size" role="group" aria-label="PDF page size">
              {#each PDF_PAGE_SIZE_OPTIONS as { value, label } (value)}
                <button
                  type="button"
                  class="pdf-page-size-button"
                  class:active={pdfPageSize === value}
                  aria-pressed={pdfPageSize === value}
                  onclick={() => handlePdfPageSizeChange(value)}
                >
                  {label}
                </button>
              {/each}
            </div>
            <button
              type="button"
              class="export-button"
              class:disabled={!exportFor("musicxml") || pdfExporting}
              disabled={!exportFor("musicxml") || pdfExporting}
              onclick={handleExportPdfClick}
            >
              {pdfExporting ? "Generating PDF…" : "Export PDF"}
            </button>
            {#if exportSavedFormat.pdf}
              <span class="export-status">Saved</span>
            {/if}
            {#if exportErrorByFormat.pdf}
              <p class="field-error">{exportErrorByFormat.pdf}</p>
            {/if}
          </div>
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

  /* --- PDF page size picker ---------------------------------------------- */

  .pdf-page-size {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    align-self: center;
  }

  .pdf-page-size-button {
    flex: 1;
    background: transparent;
    border: none;
    color: var(--dim);
    padding: 4px 10px;
    font-size: 12px;
    font-weight: 600;
    cursor: pointer;
  }

  .pdf-page-size-button:not(:first-child) {
    border-left: 1px solid var(--border);
  }

  .pdf-page-size-button.active {
    background: var(--accent);
    color: #1e1d21;
  }

  /* --- Export native-save confirmation/error (Task 9) -------------------- */

  .export-row {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .export-status {
    text-align: center;
    font-size: 12px;
    font-weight: 600;
    color: #7fb069;
    animation: export-status-fade 2s ease forwards;
  }

  @keyframes export-status-fade {
    0% {
      opacity: 1;
    }
    70% {
      opacity: 1;
    }
    100% {
      opacity: 0;
    }
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
