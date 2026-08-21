// Client-side, offline PDF export of the currently-loaded score, rendered
// WYSIWYG from OSMD's own paged layout (EngravingRules page format
// "A4_P") through jsPDF + svg2pdf.js -- no backend involvement, no system
// dependencies. (music21's PDF path needs MuseScore/LilyPond installed on
// the user's machine -- explicitly rejected for this feature.)
//
// Split into two seams on purpose:
//   1. renderScorePagesToSvg() -- talks to a FRESH, offscreen OSMD instance
//      and the real DOM. This project's vitest config runs
//      `environment: "node"` (see vitest.config.ts) with no jsdom
//      installed, so this function cannot execute under `vitest run` at
//      all -- verified only by hand in the real Tauri webview, not by an
//      automated test. See exportPdf.test.ts for exactly what IS covered.
//   2. assemblePdfFromSvgPages() -- pure orchestration (jsPDF/svg2pdf.js
//      calls only, no OSMD, no live layout, no real DOM measurements) --
//      unit-tested with fake SVGElement stand-ins and mocked
//      "jspdf"/"svg2pdf.js" modules, the same pattern saveExport.test.ts
//      already uses for the Tauri plugins.
// exportScoreToPdf() just wires the two together.
//
// Deliberately renders into a BRAND-NEW OSMD instance/container rather
// than reusing Notation.svelte's on-screen one: that sidesteps every
// re-render/cursor gotcha documented there (osmd.render() constructs a
// brand-new Cursor every time; a handle must re-read `.cursor` lazily;
// zoom/TAB-visibility changes require re-asserting cursor state -- see
// Notation.svelte's own comments and docs/superpowers/SESSION-HANDOFF.md)
// simply by not having a cursor and not being the thing on screen.

import { jsPDF } from "jspdf";
import { svg2pdf } from "svg2pdf.js";
import { OpenSheetMusicDisplay } from "opensheetmusicdisplay";

/** OSMD's own standard page-format id for A4 portrait. Verified against
 * the installed 2.1.2 package (the public .d.ts only documents
 * `pageFormat?: string` with "e.g. A4 P" as a comment, which is not
 * enough to know the id is well-formed): grepping the installed
 * `opensheetmusicdisplay.min.js` shows `StringToPageFormat()` replaces
 * spaces with underscores before lookup (so "A4 P" and "A4_P" are
 * equivalent) and `OpenSheetMusicDisplay.PageFormatStandards.A4_P` is
 * defined as `new PageFormat(210, 297, "A4_P")` -- i.e. millimeters,
 * matching A4_WIDTH_MM/A4_HEIGHT_MM below exactly. Passed via
 * `new OpenSheetMusicDisplay(container, { pageFormat: ... })`, the
 * documented way to select it (`OSMDOptions.d.ts`: "Used by
 * setOptions({pageFormat: "A4_P"})"). */
const OSMD_PAGE_FORMAT_ID = "A4_P";

/** A4 portrait, in millimeters -- MUST match OSMD_PAGE_FORMAT_ID's own
 * dimensions above. This is what makes each generated PDF page the same
 * physical paper size OSMD just laid the score out for: passed verbatim
 * as jsPDF's page `format` (unit "mm") and as svg2pdf's per-page
 * width/height options. */
const A4_WIDTH_MM = 210;
const A4_HEIGHT_MM = 297;

/** Offscreen render container width, in CSS px. OSMD's non-"Endless"
 * PageFormat layout scales each page's SVG to fill the container's own
 * `offsetWidth` -- confirmed against the installed bundle's
 * `createOrRefreshRenderBackend()` (`e = this.container.offsetWidth`,
 * then, when a PageFormat is set, `t = e / rules.PageFormat.aspectRatio`
 * for the page height) -- so the container needs a real, laid-out width
 * for OSMD to size pages against; a detached or zero-width container
 * renders empty/zero-size pages. 794px approximates A4's width at a
 * 96dpi-equivalent scale (210mm / 25.4mm-per-inch * 96dpi ~= 793.7),
 * giving OSMD normal-looking engraving to lay out before svg2pdf scales
 * the result back down to the physical A4_WIDTH_MM on the PDF page. */
const RENDER_CONTAINER_WIDTH_PX = 794;

/** Renders `xmlText` into a brand-new, OFFSCREEN OSMD instance configured
 * for paged (A4 portrait) output, and returns one SVGElement per page in
 * page order. Each returned element is a detached clone -- it stays
 * usable after this function tears its temporary container down.
 *
 * DOM/webview-only -- see this module's header comment.
 */
export async function renderScorePagesToSvg(xmlText: string): Promise<SVGElement[]> {
  const container = document.createElement("div");
  // Positioned off-canvas rather than `display:none` -- a `display:none`
  // element reports 0 for `offsetWidth`, which (per the comment on
  // RENDER_CONTAINER_WIDTH_PX above) would make every OSMD page
  // zero-width. Real (off-screen) layout is required, not just presence
  // in the DOM.
  container.style.position = "fixed";
  container.style.top = "0";
  container.style.left = "-99999px";
  container.style.width = `${RENDER_CONTAINER_WIDTH_PX}px`;
  document.body.appendChild(container);

  const osmd = new OpenSheetMusicDisplay(container, {
    autoResize: false,
    drawTitle: true,
    pageFormat: OSMD_PAGE_FORMAT_ID,
  });

  try {
    await osmd.load(xmlText);
    osmd.render();

    // Each page's backend appends its own `<div id="osmdCanvasPageN">`
    // (containing `<svg id="osmdSvgPageN">`) directly as a child of
    // `container`, in page order -- confirmed against the installed
    // 2.1.2 bundle's `SvgVexFlowBackend.initialize()`. `osmd.drawer` /
    // `drawer.Backends` are `protected` (not part of the public API, and
    // not accessible in a type-checked way from outside the class), so
    // this reads the actual DOM OSMD produces instead of reaching into
    // internals.
    const svgs = Array.from(container.querySelectorAll<SVGSVGElement>('svg[id^="osmdSvgPage"]'));
    if (svgs.length === 0) throw new Error("OSMD produced no pages to export.");
    // Cloned so the returned elements survive this function's own
    // cleanup below -- removing `container` would otherwise detach them
    // too.
    return svgs.map((svg) => svg.cloneNode(true) as SVGElement);
  } finally {
    osmd.clear();
    container.remove();
  }
}

/** Assembles one A4-portrait PDF page per entry in `pages` (in order) via
 * jsPDF + svg2pdf.js, and returns the finished document as bytes.
 *
 * Pure orchestration -- no OSMD, no live layout, no real DOM measurements
 * -- so it's unit-testable with fake `SVGElement` stand-ins and mocked
 * "jspdf"/"svg2pdf.js" modules (see exportPdf.test.ts).
 *
 * Throws if `pages` is empty: an empty PDF is never a useful result for
 * this feature, and the real caller (exportScoreToPdf) already gets
 * "OSMD produced no pages to export" from renderScorePagesToSvg() before
 * this could ever run with zero pages.
 */
export async function assemblePdfFromSvgPages(pages: readonly SVGElement[]): Promise<Uint8Array> {
  if (pages.length === 0) throw new Error("No pages to export.");

  const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: [A4_WIDTH_MM, A4_HEIGHT_MM] });

  for (let i = 0; i < pages.length; i += 1) {
    // First page: jsPDF's constructor above already created it.
    if (i > 0) doc.addPage([A4_WIDTH_MM, A4_HEIGHT_MM], "portrait");
    // Pages must render onto the document in order -- svg2pdf mutates
    // `doc`'s CURRENT page, so this can't be parallelized across pages.
    // eslint-disable-next-line no-await-in-loop
    await svg2pdf(pages[i], doc, { x: 0, y: 0, width: A4_WIDTH_MM, height: A4_HEIGHT_MM });
  }

  return new Uint8Array(doc.output("arraybuffer"));
}

/** Full export: fetch is the CALLER's job, same as every other consumer of
 * the project's MusicXML export (reuse the identical
 * `fetch(api.exportDownloadUrl(musicxmlExportId), { cache: "no-store" })`
 * ScoreView.svelte's loadScore()/refreshAfterEdit() already use -- see
 * docs/superpowers/SESSION-HANDOFF.md's OSMD gotcha #2 on why
 * `cache: "no-store"` is required for that URL). This just renders +
 * assembles what the caller already fetched. */
export async function exportScoreToPdf(xmlText: string): Promise<Uint8Array> {
  const pages = await renderScorePagesToSvg(xmlText);
  return assemblePdfFromSvgPages(pages);
}
