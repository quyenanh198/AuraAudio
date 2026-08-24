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

// --- Bug 2 fix: TAB fret numbers (and other VexFlow text) invisible in the
// exported PDF -----------------------------------------------------------
//
// PROVEN root cause (see the session report for the full investigation,
// including page renders at 1 page and at 48 pages): OSMD's TAB rendering
// goes through VexFlow, and VexFlow's own SVG backend
// (`Vex.Flow.SVGContext`, confirmed directly against the installed 2.1.2
// bundle's `setRawFont()`) writes text `font-size` attributes as
// **point-sized strings** -- e.g. `"10pt"` for a fret number, `"14pt"` for
// a tempo mark -- because VexFlow's own internal font specs are always
// `"<N>pt <family>"`. OSMD's OWN native labels (the project title, the
// "Guitar" instrument name) go through a different rendering path and end
// up with a plain unitless/px `font-size` ("20px") -- those were never
// affected.
//
// svg2pdf.js 2.7.0's own `toPixels()` unit parser (vendored in
// node_modules, not ours to edit) recognizes exactly two forms for a
// `font-size` value: a bare number ("20") or an explicit "px" suffix
// ("20px") -- verified directly against its regex,
// `/^([\-0-9.]+)(px|)$/`. Any other unit, "pt" included, falls through to
// its final `return 0`. That 0 flows straight into the PDF's `Tf` (set
// font + SIZE) operator for that text run. svg2pdf.js still emits a
// perfectly correct `Tj` (show text) operator right after it, with the
// right glyph content and position -- so the fret-number text IS honestly
// present in the PDF bytes, just rendered at font-size 0: invisible, in
// every PDF viewer, on EVERY page. This was proven NOT to be a
// dropped-text bug and NOT a large-score/pagination bug (a hypothesis
// this task's brief raised): a 1-page, 4-note fixture reproduces it
// identically to a 48-page, 950-measure one -- the notation staff's
// noteheads/stems/clefs survive regardless of score size because they're
// drawn as PATH glyphs (`renderGlyph`), never as `<text>`, so they never
// go through this code path at all. That is exactly why only the TAB
// numbers (and, incidentally, the tempo mark) vanished while the
// notation staff above them kept rendering normally.
//
// Fixed HERE (rewriting the cloned SVG before it reaches svg2pdf.js)
// rather than by patching the vendored package: `ptFontSizeToPx()` is the
// pure half (unit-testable under this project's Node-only vitest config
// -- see exportPdf.test.ts); `normalizeSvgFontSizeUnits()` is the
// DOM-walking half that applies it to a rendered page, called from
// renderScorePagesToSvg() below on the SAME DOM-only seam as everything
// else in that function.

/** 1pt = 4/3 px at the 96-CSS-px-per-inch scale svg2pdf.js's own
 * `toPixels()` assumes (same dpi baseline as CSS_PX_PER_MM above: 96px per
 * 25.4mm/72pt-per-inch, i.e. 96/72 = 4/3 px per pt). */
const PX_PER_PT = 4 / 3;

/** Rewrites a CSS `font-size` value ending in the literal unit `"pt"` (the
 * exact form VexFlow's SVG backend emits -- see this section's header
 * comment) to the equivalent bare-px numeric string svg2pdf.js's own unit
 * parser understands. Any other value (already unitless, "px"-suffixed,
 * "em"-suffixed, or simply not parseable as "<number>pt") is returned
 * UNCHANGED -- this only ever touches the exact shape that was silently
 * zeroing out, never anything svg2pdf.js already handles correctly. Pure
 * string/number logic, no DOM -- see exportPdf.test.ts. */
export function ptFontSizeToPx(value: string): string {
  const match = value.match(/^(-?[0-9]*\.?[0-9]+)pt$/);
  if (match === null) return value;
  const points = parseFloat(match[1]);
  if (!Number.isFinite(points)) return value;
  // Rounded to a few decimal places -- svg2pdf.js's own px regex accepts
  // any number of decimals, but an exact binary-float tail (e.g.
  // "13.333333333333334") is pure noise in the SVG attribute for no
  // precision benefit at print scale.
  const px = Math.round(points * PX_PER_PT * 1000) / 1000;
  return `${px}px`;
}

/** Walks `root` and every descendant, rewriting any `font-size` attribute
 * that carries a "pt" value (see ptFontSizeToPx()) to its px equivalent,
 * in place. DOM-only (Element.querySelectorAll/getAttribute/setAttribute)
 * -- called from renderScorePagesToSvg() below, never unit-tested
 * directly (same limitation as the rest of that function -- see this
 * file's header comment); ptFontSizeToPx() itself carries the tested
 * logic. */
function normalizeSvgFontSizeUnits(root: Element): void {
  const candidates: Element[] = [root, ...Array.from(root.querySelectorAll("[font-size]"))];
  for (const el of candidates) {
    const current = el.getAttribute("font-size");
    if (current === null) continue;
    const next = ptFontSizeToPx(current);
    if (next !== current) el.setAttribute("font-size", next);
  }
}

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
    // Bug 1 fix: OSMD's title/subtitle text goes through svg2pdf.js into
    // jsPDF's 14 standard fonts, which are WinAnsi-only -- Vietnamese
    // diacritics and CJK characters (this app's actual project titles;
    // see the bug report) garble into mojibake. This ALSO used to draw
    // the title twice at two sizes (music21 wrote the same text into both
    // <work><work-title> and <movement-title>; see
    // musicxml/export.py's _apply_metadata for that fix). Verified against
    // the installed 2.1.2 `OSMDOptions.d.ts`: "Whether to draw the title
    // of the piece. If false, disables drawing Subtitle as well." -- i.e.
    // `drawTitle: false` alone (no separate `drawSubtitle` needed) fully
    // removes OSMD/svg2pdf from the title's rendering path for BOTH
    // elements, regardless of what the fetched MusicXML's title metadata
    // says. The title is instead rendered as a high-DPI Unicode-correct
    // raster strip and composited directly onto the PDF -- see
    // buildTitleImage()/assemblePdfFromSvgPages() below.
    //
    // Matches Notation.svelte's on-screen OSMD instance, which already
    // uses `drawTitle: false` (verified directly) -- so this makes the
    // offscreen PDF-export instance consistent with the on-screen view
    // rather than introducing new title-drawing behavior; the on-screen
    // view is unaffected by this change.
    drawTitle: false,
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
    //
    // Bug 2 fix: normalized IN PLACE on each clone, before returning --
    // see normalizeSvgFontSizeUnits()'s own doc comment above for why this
    // must happen here (fret numbers, tempo marks, and any other
    // VexFlow-drawn text would otherwise render at font-size 0, invisible,
    // once svg2pdf.js gets these pages).
    return svgs.map((svg) => {
      const clone = svg.cloneNode(true) as SVGElement;
      normalizeSvgFontSizeUnits(clone);
      return clone;
    });
  } finally {
    osmd.clear();
    container.remove();
  }
}

/** A pre-rendered title strip ready to composite onto page 1 of the PDF --
 * see buildTitleImage() below for how this gets produced. Plain data (a
 * PNG data URL + its physical height) rather than a live canvas so
 * assemblePdfFromSvgPages() stays DOM-free and unit-testable (see its own
 * doc comment). */
export interface TitleImage {
  /** `data:image/png;base64,...` -- passed straight to jsPDF's addImage(). */
  readonly dataUrl: string;
  /** Physical height of the strip in mm. Its width always equals the full
   * page width (format.widthMm) -- the text within is already centered by
   * buildTitleImage(), so no horizontal offset math is needed here. */
  readonly heightMm: number;
}

/** Assembles one portrait PDF page per entry in `pages` (in order), sized
 * to `pageSize`, via jsPDF + svg2pdf.js, and returns the finished document
 * as bytes.
 *
 * Pure orchestration -- no OSMD, no live layout, no real DOM measurements
 * -- so it's unit-testable with fake `SVGElement` stand-ins and mocked
 * "jspdf"/"svg2pdf.js" modules (see exportPdf.test.ts). `titleImage` is
 * already-rendered DATA (see the TitleImage doc comment), not a live
 * canvas, which is what keeps this function itself DOM-free.
 *
 * Every page gets the SAME jsPDF page format AND the same svg2pdf
 * width/height, both taken from `PDF_PAGE_FORMATS[pageSize]` -- so the
 * physical PDF page and the area svg2pdf renders into always share the
 * exact same aspect ratio as each other (no stretch mismatch), and (since
 * OSMD itself laid the SVG out at that same aspect ratio via
 * `osmdPageFormatId` in renderScorePagesToSvg()) as the score's own
 * engraved page too -- EXCEPT page 1 when `titleImage` is given (Bug 1
 * fix): that page's music is uniformly scaled down (both dimensions by
 * the SAME factor, so its aspect ratio -- and therefore its engraving --
 * is never distorted, just slightly smaller) to leave `titleImage`'s own
 * exact height free at the top, where the raster title strip is drawn.
 * This sidesteps needing to know anything about OSMD's own internal
 * title-margin reservation (its exact size isn't part of OSMD's public
 * API, and verifying it would require the real webview DOM this project's
 * vitest config can't provide -- see this file's header comment): the
 * scale-down here is derived purely from `titleImage.heightMm`, which
 * this function already has as a plain number, so it's exact by
 * construction regardless of what OSMD's own layout did. Pages after the
 * first are never affected -- "Multi-page: title on page 1 only".
 *
 * Throws if `pages` is empty: an empty PDF is never a useful result for
 * this feature, and the real caller (exportScoreToPdf) already gets
 * "OSMD produced no pages to export" from renderScorePagesToSvg() before
 * this could ever run with zero pages.
 */
export async function assemblePdfFromSvgPages(
  pages: readonly SVGElement[],
  pageSize: PdfPageSize = DEFAULT_PDF_PAGE_SIZE,
  titleImage: TitleImage | null = null,
): Promise<Uint8Array> {
  if (pages.length === 0) throw new Error("No pages to export.");

  const format = PDF_PAGE_FORMATS[pageSize];
  const pdfFormat: [number, number] = [format.widthMm, format.heightMm];
  const doc = new jsPDF({ orientation: "portrait", unit: "mm", format: pdfFormat });

  for (let i = 0; i < pages.length; i += 1) {
    // First page: jsPDF's constructor above already created it.
    if (i > 0) doc.addPage(pdfFormat, "portrait");

    if (i === 0 && titleImage !== null) {
      // Full page width, top-anchored -- buildTitleImage() already
      // centered the text horizontally within that width, so this needs
      // no x offset.
      doc.addImage(titleImage.dataUrl, "PNG", 0, 0, format.widthMm, titleImage.heightMm);

      // Uniform (non-distorting) scale-down: shrinking ONLY the height
      // given to svg2pdf while keeping the full page width (like the
      // titleImage === null branch below does) would stretch the
      // engraving vertically-compressed, since svg2pdf maps the source
      // SVG's own width/height directly onto whatever box it's given.
      // Scaling width by the SAME factor as height keeps the box's own
      // aspect ratio equal to format.widthMm/format.heightMm -- the exact
      // aspect ratio OSMD laid this SVG out at (see this function's own
      // doc comment above) -- so nothing distorts, the page just renders
      // slightly smaller and centered in the remaining width.
      const contentHeightMm = format.heightMm - titleImage.heightMm;
      const contentWidthMm = format.widthMm * (contentHeightMm / format.heightMm);
      const xOffsetMm = (format.widthMm - contentWidthMm) / 2;
      // eslint-disable-next-line no-await-in-loop
      await svg2pdf(pages[i], doc, { x: xOffsetMm, y: titleImage.heightMm, width: contentWidthMm, height: contentHeightMm });
      continue;
    }

    // Pages must render onto the document in order -- svg2pdf mutates
    // `doc`'s CURRENT page, so this can't be parallelized across pages.
    // eslint-disable-next-line no-await-in-loop
    await svg2pdf(pages[i], doc, { x: 0, y: 0, width: format.widthMm, height: format.heightMm });
  }

  return new Uint8Array(doc.output("arraybuffer"));
}

// --- Title raster (Bug 1 fix) -----------------------------------------
//
// The project title is rendered OURSELVES onto an offscreen <canvas>,
// using the webview's own font stack (system-ui resolves to a real font
// with full Vietnamese + CJK coverage on every desktop OS this app ships
// on -- no bundled/CDN font needed, staying offline), and composited as a
// PNG image -- never routed through jsPDF's WinAnsi-only standard fonts.
// Split the same way the rest of this file is split (see header comment):
// wrapTitleLines() is pure text-layout math, unit-testable with a mocked
// measureWidth function; buildTitleImage() is the real-canvas half that
// calls it, DOM-only like renderScorePagesToSvg() and equally untestable
// under this project's `environment: "node"` vitest config (see
// exportPdf.test.ts for exactly what IS covered).

/** ~16pt print title, expressed in mm (1pt = 1/72in) so it can be
 * converted to raster px at whatever DPI buildTitleImage() renders at,
 * without ever going through a CSS-px/print-pt mismatch. */
const TITLE_FONT_SIZE_MM = (16 / 72) * 25.4;
const TITLE_LINE_HEIGHT_FACTOR = 1.3;
/** "2-3 lines max with ellipsis beyond" -- see this module's header. */
const TITLE_MAX_LINES = 3;
/** Raster resolution for the title strip -- print-quality (300dpi), high
 * enough that the strip stays crisp when placed into the PDF at its full
 * physical mm size (see buildTitleImage()'s use of MM_PER_INCH below). */
const TITLE_RASTER_DPI = 300;
const MM_PER_INCH = 25.4;
const TITLE_RASTER_PX_PER_MM = TITLE_RASTER_DPI / MM_PER_INCH;
/** Blank margin on each side of the title strip, in mm -- independent of
 * OSMD's own page margins (this is a different visual element entirely,
 * see assemblePdfFromSvgPages()'s doc comment on why no OSMD margin needs
 * to be known here at all). */
const TITLE_SIDE_MARGIN_MM = 12;
const TITLE_VERTICAL_PADDING_MM = 3;
/** system-ui resolves to the OS's real UI font (San Francisco / Segoe UI /
 * Ubuntu Sans / etc.), which on every desktop OS this Tauri app ships on
 * already covers Vietnamese + CJK -- no bundled or CDN font needed. The
 * explicit fallbacks are defense-in-depth for whichever webview engine is
 * in play, not a requirement for correctness. */
const TITLE_FONT_STACK =
  "system-ui, -apple-system, 'Segoe UI', 'Noto Sans', 'PingFang SC', 'Microsoft YaHei', 'Malgun Gothic', sans-serif";

/** Splits `text` into wrap tokens: each run of non-CJK, non-whitespace
 * characters is ONE token (so Vietnamese/Latin words never break
 * mid-word), each individual CJK ideograph/kana/hangul character is its
 * OWN token (so CJK -- which uses no spaces -- can still wrap between any
 * two characters, matching how CJK text is conventionally typeset), and
 * whitespace runs are their own tokens (collapsed to a single space when
 * kept, dropped at line starts). Verified directly: V8 (this app's
 * webview engine, and Node's, for the unit tests below) supports `\p{Script=...}`
 * Unicode property escapes with the `u` flag. */
function tokenizeTitle(text: string): string[] {
  return (
    text.match(
      /\s+|[\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]|[^\s\p{Script=Han}\p{Script=Hiragana}\p{Script=Katakana}\p{Script=Hangul}]+/gu,
    ) ?? []
  );
}

/** Shortens `line` (assumed already <= maxWidthPx on its own) so that
 * `line + ellipsis` fits within maxWidthPx, trimming from the end one
 * character at a time. Falls back to a bare ellipsis if even that alone
 * doesn't fit (pathological: an extremely narrow maxWidthPx). */
function truncateWithEllipsis(
  line: string,
  maxWidthPx: number,
  measureWidth: (text: string) => number,
  ellipsis: string,
): string {
  if (measureWidth(line + ellipsis) <= maxWidthPx) return `${line}${ellipsis}`;
  let end = line.length;
  while (end > 0 && measureWidth(`${line.slice(0, end)}${ellipsis}`) > maxWidthPx) {
    end -= 1;
  }
  return end > 0 ? `${line.slice(0, end).trimEnd()}${ellipsis}` : ellipsis;
}

/** Pure text-wrapping: greedily packs `text`'s tokens (see tokenizeTitle())
 * into lines no wider than `maxWidthPx` (per the injected `measureWidth`),
 * then caps the result at `maxLines`, truncating the last kept line with
 * `ellipsis` if content had to be dropped. `measureWidth` is injected
 * (rather than this function touching `document`/`canvas` itself)
 * specifically so it's unit-testable with a deterministic fake under this
 * project's DOM-less `environment: "node"` vitest config -- see
 * buildTitleImage() below for the real `CanvasRenderingContext2D.
 * measureText`-backed caller. Returns `[]` for blank/whitespace-only
 * input or a non-positive `maxLines`. */
export function wrapTitleLines(
  measureWidth: (text: string) => number,
  text: string,
  maxWidthPx: number,
  maxLines: number = TITLE_MAX_LINES,
  ellipsis: string = "…",
): string[] {
  const trimmed = text.trim();
  if (trimmed === "" || maxLines <= 0) return [];

  const tokens = tokenizeTitle(trimmed);
  const allLines: string[] = [];
  let current = "";

  for (const token of tokens) {
    if (/^\s+$/.test(token)) {
      // Collapse any whitespace run to a single space, and only keep it
      // if there's already content on the current line (never start a
      // line with a leading space).
      if (current !== "") current += " ";
      continue;
    }
    const candidate = current === "" ? token : current + token;
    if (current !== "" && measureWidth(candidate) > maxWidthPx) {
      // trimEnd: `current` may carry the single trailing space collapsed
      // in above (kept there so it counted towards `candidate`'s measured
      // width, matching how a real word-space would) -- never wanted in
      // the pushed line itself.
      allLines.push(current.trimEnd());
      current = token;
    } else {
      current = candidate;
    }
  }
  if (current !== "") allLines.push(current.trimEnd());

  if (allLines.length <= maxLines) return allLines;

  const kept = allLines.slice(0, maxLines);
  kept[kept.length - 1] = truncateWithEllipsis(kept[kept.length - 1], maxWidthPx, measureWidth, ellipsis);
  return kept;
}

/** Renders `title` onto an offscreen `<canvas>` at print quality and
 * returns it as a `TitleImage` (PNG data URL + physical height), or
 * `null` when there's no title to draw (blank/whitespace-only `title`, or
 * the webview's canvas 2D context is unavailable for some reason) -- both
 * cases degrade to "page 1 renders like every other page", never a
 * thrown error, since a missing title strip is cosmetic, not fatal to the
 * export.
 *
 * DOM/webview-only, like renderScorePagesToSvg() -- see this module's
 * header comment; not covered by this project's Node-only vitest suite.
 */
export function buildTitleImage(title: string, pageSize: PdfPageSize = DEFAULT_PDF_PAGE_SIZE): TitleImage | null {
  const trimmed = title.trim();
  if (trimmed === "") return null;

  const format = PDF_PAGE_FORMATS[pageSize];
  const canvasWidthPx = Math.round(format.widthMm * TITLE_RASTER_PX_PER_MM);
  const fontSizePx = Math.round(TITLE_FONT_SIZE_MM * TITLE_RASTER_PX_PER_MM);
  const lineHeightPx = Math.round(fontSizePx * TITLE_LINE_HEIGHT_FACTOR);
  const maxWidthPx = (format.widthMm - 2 * TITLE_SIDE_MARGIN_MM) * TITLE_RASTER_PX_PER_MM;
  const fontSpec = `600 ${fontSizePx}px ${TITLE_FONT_STACK}`;

  const measureCtx = document.createElement("canvas").getContext("2d");
  if (measureCtx === null) return null;
  measureCtx.font = fontSpec;
  const lines = wrapTitleLines((text) => measureCtx.measureText(text).width, trimmed, maxWidthPx, TITLE_MAX_LINES);
  if (lines.length === 0) return null;

  const paddingPx = Math.round(TITLE_VERTICAL_PADDING_MM * TITLE_RASTER_PX_PER_MM);
  const canvasHeightPx = paddingPx * 2 + lines.length * lineHeightPx;

  const canvas = document.createElement("canvas");
  canvas.width = canvasWidthPx;
  canvas.height = canvasHeightPx;
  const ctx = canvas.getContext("2d");
  if (ctx === null) return null;

  // Opaque white background: this strip sits at the very top of page 1,
  // above the (uniformly scaled-down, never overlapping) music content --
  // an opaque fill here is simplest and matches the page's own white
  // background, avoiding any dependency on PNG alpha support round-
  // tripping correctly through jsPDF's addImage().
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, canvasWidthPx, canvasHeightPx);
  ctx.font = fontSpec;
  ctx.fillStyle = "#000000";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  lines.forEach((line, index) => {
    ctx.fillText(line, canvasWidthPx / 2, paddingPx + lineHeightPx * index + lineHeightPx / 2);
  });

  return { dataUrl: canvas.toDataURL("image/png"), heightMm: canvasHeightPx / TITLE_RASTER_PX_PER_MM };
}

/** Full export: fetch is the CALLER's job, same as every other consumer of
 * the project's MusicXML export (reuse the identical
 * `fetch(api.exportDownloadUrl(musicxmlExportId), { cache: "no-store" })`
 * ScoreView.svelte's loadScore()/refreshAfterEdit() already use -- see
 * docs/superpowers/SESSION-HANDOFF.md's OSMD gotcha #2 on why
 * `cache: "no-store"` is required for that URL). This just renders +
 * assembles what the caller already fetched, for the given page size
 * (defaults to A4).
 *
 * `title` (Bug 1 fix): the project's own title, from the app's existing
 * state (the caller -- Sidebar.svelte -- already has it as a prop; no new
 * fetch needed), rendered as a raster strip on page 1 -- see
 * buildTitleImage() above. Optional/defaults to "" (no title strip) so
 * this stays backwards compatible for any other caller. */
export async function exportScoreToPdf(
  xmlText: string,
  pageSize: PdfPageSize = DEFAULT_PDF_PAGE_SIZE,
  title: string = "",
): Promise<Uint8Array> {
  const pages = await renderScorePagesToSvg(xmlText, pageSize);
  const titleImage = buildTitleImage(title, pageSize);
  return assemblePdfFromSvgPages(pages, pageSize, titleImage);
}
