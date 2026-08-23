// Client-side, offline PDF export of the currently-loaded score, rendered
// WYSIWYG from OSMD's own paged layout (EngravingRules page format --
// "A4_P" or "Letter_P", selectable) through jsPDF + svg2pdf.js -- no
// backend involvement, no system dependencies. (music21's PDF path needs
// MuseScore/LilyPond installed on the user's machine -- explicitly
// rejected for this feature.)
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

/** The two page sizes this export offers. Matches OSMD's own
 * PageFormatStandards ids one-to-one (see PDF_PAGE_FORMATS below) -- there
 * is no third "custom" option, so no EngravingRules.setCustomPageFormat()
 * path is needed here. */
export type PdfPageSize = "A4" | "Letter";

export const DEFAULT_PDF_PAGE_SIZE: PdfPageSize = "A4";

interface PdfPageFormat {
  /** OSMD's own standard page-format id, passed via `new
   * OpenSheetMusicDisplay(container, { pageFormat: ... })` (the documented
   * way to select it -- `OSMDOptions.d.ts`: "Used by
   * setOptions({pageFormat: "A4_P"})"). */
  readonly osmdPageFormatId: string;
  /** Physical page dimensions in millimeters -- MUST match the OSMD id's
   * own dimensions exactly. This is what makes each generated PDF page the
   * same physical paper size OSMD just laid the score out for: passed
   * verbatim as jsPDF's page `format` (unit "mm") and as svg2pdf's
   * per-page width/height options. */
  readonly widthMm: number;
  readonly heightMm: number;
}

/** Verified against the installed 2.1.2 package (the public .d.ts only
 * documents `pageFormat?: string` with "e.g. A4 P" as a comment and a
 * doc-comment example mentioning `PageFormatStandards["Letter_L"]", which
 * is not enough on its own to know a portrait Letter id actually exists or
 * is well-formed): grepping the installed
 * `opensheetmusicdisplay.min.js` shows `OpenSheetMusicDisplay.
 * PageFormatStandards` is defined with BOTH
 * `A4_P: new PageFormat(210, 297, "A4_P")` and
 * `Letter_P: new PageFormat(215.9, 279.4, "Letter_P")` -- i.e. OSMD
 * already ships a standard Letter-portrait id, in millimeters, matching
 * the widthMm/heightMm below exactly (215.9mm x 279.4mm = 8.5in x 11in).
 * No custom PageFormat construction (EngravingRules.setCustomPageFormat)
 * is needed for either size. `StringToPageFormat()` (used internally by
 * `setPageFormat`/the `pageFormat` option) replaces spaces with
 * underscores before lookup, so "A4 P"/"A4_P" and "Letter P"/"Letter_P"
 * are equivalent -- this uses the underscore form directly. */
const PDF_PAGE_FORMATS: Record<PdfPageSize, PdfPageFormat> = {
  A4: { osmdPageFormatId: "A4_P", widthMm: 210, heightMm: 297 },
  Letter: { osmdPageFormatId: "Letter_P", widthMm: 215.9, heightMm: 279.4 },
};

/** CSS px per physical mm at a 96dpi-equivalent scale (96 / 25.4mm-per-inch
 * ~= 3.7795). Used to size the offscreen render container in proportion to
 * whichever page format is selected -- see renderContainerWidthPx() below.
 * This is the same ratio the previous A4-only implementation used
 * implicitly (794px derived from 210mm this same way); pulling it out as
 * its own constant is what lets the container size follow the FORMAT
 * instead of being hardcoded to one. */
const CSS_PX_PER_MM = 96 / 25.4;

/** Offscreen render container width, in CSS px, for the given page format.
 * OSMD's non-"Endless" PageFormat layout scales each page's SVG to fill
 * the container's own `offsetWidth` -- confirmed against the installed
 * bundle's `createOrRefreshRenderBackend()` (`e = this.container.
 * offsetWidth`, then, when a PageFormat is set, `t = e /
 * rules.PageFormat.aspectRatio` for the page height) -- so the container
 * needs a real, laid-out width for OSMD to size pages against; a detached
 * or zero-width container renders empty/zero-size pages.
 *
 * Beyond that resize step, the SAME container-derived width also becomes
 * `sheet.pageWidth` (confirmed in the installed bundle's render path:
 * `this.sheet.pageWidth = this.container.offsetWidth / this.zoom / 10`),
 * which is the internal engraving-scale page width OSMD lays out staff
 * lines, noteheads, and text against -- i.e. it is NOT purely a display
 * resolution knob, it directly sets how large the engraving renders
 * relative to the page. A width derived only from A4 (794px, ~210mm at
 * 96dpi) reused unchanged for a Letter page (215.9mm, ~2.8% wider) would
 * make Letter's engraving render very slightly larger relative to its own
 * page than A4's does relative to A4's -- small, but real, and exactly the
 * kind of hardcoding this computes away from by deriving the container
 * width from the SELECTED format's own physical width every time, at the
 * same effective 96dpi scale for both. */
function renderContainerWidthPx(format: PdfPageFormat): number {
  return Math.round(format.widthMm * CSS_PX_PER_MM);
}

/** Renders `xmlText` into a brand-new, OFFSCREEN OSMD instance configured
 * for paged (portrait) output at the given page size, and returns one
 * SVGElement per page in page order. Each returned element is a detached
 * clone -- it stays usable after this function tears its temporary
 * container down.
 *
 * DOM/webview-only -- see this module's header comment.
 */
export async function renderScorePagesToSvg(
  xmlText: string,
  pageSize: PdfPageSize = DEFAULT_PDF_PAGE_SIZE,
): Promise<SVGElement[]> {
  const format = PDF_PAGE_FORMATS[pageSize];

  const container = document.createElement("div");
  // Positioned off-canvas rather than `display:none` -- a `display:none`
  // element reports 0 for `offsetWidth`, which (per the comment on
  // renderContainerWidthPx() above) would make every OSMD page
  // zero-width. Real (off-screen) layout is required, not just presence
  // in the DOM.
  container.style.position = "fixed";
  container.style.top = "0";
  container.style.left = "-99999px";
  container.style.width = `${renderContainerWidthPx(format)}px`;
  document.body.appendChild(container);

  const osmd = new OpenSheetMusicDisplay(container, {
    autoResize: false,
    drawTitle: true,
    pageFormat: format.osmdPageFormatId,
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
    // too. Safe for the detached clone to stay detached all the way to
    // svg2pdf() in assemblePdfFromSvgPages(): svg2pdf does its own text
    // measurement via a SEPARATE hidden `<svg><text>` node it appends to
    // `document.body` itself (and removes when done, `cleanupTextMeasuring()`
    // in the installed 2.7.0 bundle) -- it never relies on the passed-in
    // element itself being attached/laid-out.
    return svgs.map((svg) => svg.cloneNode(true) as SVGElement);
  } finally {
    osmd.clear();
    container.remove();
  }
}

/** Assembles one portrait PDF page per entry in `pages` (in order), sized
 * to `pageSize`, via jsPDF + svg2pdf.js, and returns the finished document
 * as bytes.
 *
 * Pure orchestration -- no OSMD, no live layout, no real DOM measurements
 * -- so it's unit-testable with fake `SVGElement` stand-ins and mocked
 * "jspdf"/"svg2pdf.js" modules (see exportPdf.test.ts).
 *
 * Every page gets the SAME jsPDF page format AND the same svg2pdf
 * width/height, both taken from `PDF_PAGE_FORMATS[pageSize]` -- so the
 * physical PDF page and the area svg2pdf renders into always share the
 * exact same aspect ratio as each other (no stretch mismatch), and (since
 * OSMD itself laid the SVG out at that same aspect ratio via
 * `osmdPageFormatId` in renderScorePagesToSvg()) as the score's own
 * engraved page too.
 *
 * Throws if `pages` is empty: an empty PDF is never a useful result for
 * this feature, and the real caller (exportScoreToPdf) already gets
 * "OSMD produced no pages to export" from renderScorePagesToSvg() before
 * this could ever run with zero pages.
 */
export async function assemblePdfFromSvgPages(
  pages: readonly SVGElement[],
  pageSize: PdfPageSize = DEFAULT_PDF_PAGE_SIZE,
): Promise<Uint8Array> {
  if (pages.length === 0) throw new Error("No pages to export.");

  const format = PDF_PAGE_FORMATS[pageSize];
  const pdfFormat: [number, number] = [format.widthMm, format.heightMm];
  const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: pdfFormat });

  for (let i = 0; i < pages.length; i += 1) {
    // First page: jsPDF's constructor above already created it.
    if (i > 0) doc.addPage(pdfFormat, "portrait");
    // Pages must render onto the document in order -- svg2pdf mutates
    // `doc`'s CURRENT page, so this can't be parallelized across pages.
    // eslint-disable-next-line no-await-in-loop
    await svg2pdf(pages[i], doc, { x: 0, y: 0, width: format.widthMm, height: format.heightMm });
  }

  return new Uint8Array(doc.output("arraybuffer"));
}

/** Full export: fetch is the CALLER's job, same as every other consumer of
 * the project's MusicXML export (reuse the identical
 * `fetch(api.exportDownloadUrl(musicxmlExportId), { cache: "no-store" })`
 * ScoreView.svelte's loadScore()/refreshAfterEdit() already use -- see
 * docs/superpowers/SESSION-HANDOFF.md's OSMD gotcha #2 on why
 * `cache: "no-store"` is required for that URL). This just renders +
 * assembles what the caller already fetched, for the given page size
 * (defaults to A4). */
export async function exportScoreToPdf(
  xmlText: string,
  pageSize: PdfPageSize = DEFAULT_PDF_PAGE_SIZE,
): Promise<Uint8Array> {
  const pages = await renderScorePagesToSvg(xmlText, pageSize);
  return assemblePdfFromSvgPages(pages, pageSize);
}
