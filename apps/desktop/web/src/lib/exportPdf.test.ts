import { beforeEach, describe, expect, it, vi } from "vitest";

// Only assemblePdfFromSvgPages is covered here -- it's the pure
// orchestration seam (see exportPdf.ts's own header comment): jsPDF and
// svg2pdf.js are mocked the same way saveExport.test.ts mocks the Tauri
// plugins, so these tests assert the CALL SEQUENCE (construct once, one
// svg2pdf call per page, one addPage between consecutive pages, output()
// last) without touching a real PDF renderer or the DOM.
//
// renderScorePagesToSvg() (real OSMD + real DOM) and exportScoreToPdf()
// (which calls it) are NOT covered here and cannot be: this project's
// vitest config runs `environment: "node"` (vitest.config.ts) with no
// jsdom installed, so there is no `document`/`SVGElement` to render
// against at all. That half is webview-only -- verified only by hand in
// the real Tauri app, not by an automated test.

const jsPDFConstructorMock = vi.fn();
const addPageMock = vi.fn();
const outputMock = vi.fn();
const svg2pdfMock = vi.fn();
const addImageMock = vi.fn();

vi.mock("jspdf", () => ({
  jsPDF: class {
    constructor(options: unknown) {
      jsPDFConstructorMock(options);
    }

    addPage(...args: unknown[]): void {
      addPageMock(...args);
    }

    addImage(...args: unknown[]): void {
      addImageMock(...args);
    }

    output(...args: unknown[]): unknown {
      return outputMock(...args);
    }
  },
}));

vi.mock("svg2pdf.js", () => ({
  svg2pdf: (...args: unknown[]) => svg2pdfMock(...args),
}));

/** Fake SVGElement stand-ins -- assemblePdfFromSvgPages never inspects an
 * element's own properties, it just threads each one through to svg2pdf()
 * unchanged, so a plain tagged object is enough to tell pages apart by
 * identity/order in assertions below. The width/height attrs mirror the
 * target page format's own aspect ratio (documentary only -- current
 * assemblePdfFromSvgPages never reads them, it always uses the explicit
 * mm width/height derived from `pageSize` for BOTH the jsPDF page and the
 * svg2pdf call, which is exactly the invariant the "consistent per format"
 * tests below assert) so a fake page always looks like a plausible real
 * OSMD page for whichever format it stands in for. */
function fakeSvgPage(label: string, widthPx: number, heightPx: number): SVGElement {
  return { __label: label, __widthPx: widthPx, __heightPx: heightPx } as unknown as SVGElement;
}

/** Page formats under test, mirroring PDF_PAGE_FORMATS in exportPdf.ts --
 * duplicated here (not imported) so a regression that silently changes the
 * real constant's values still fails these tests instead of trivially
 * passing against itself. Fake page pixel dimensions are each format's mm
 * size scaled by the SAME 96dpi-equivalent factor exportPdf.ts uses to
 * size its offscreen render container, so each fake page's own
 * width/height ratio matches its format's mm aspect ratio, same as a real
 * OSMD-rendered page would. */
const CSS_PX_PER_MM = 96 / 25.4;
const FORMATS = {
  A4: { pdfFormat: [210, 297] as [number, number] },
  Letter: { pdfFormat: [215.9, 279.4] as [number, number] },
};

describe("assemblePdfFromSvgPages", () => {
  beforeEach(() => {
    jsPDFConstructorMock.mockReset();
    addPageMock.mockReset();
    outputMock.mockReset();
    svg2pdfMock.mockReset();
    addImageMock.mockReset();
    outputMock.mockReturnValue(new ArrayBuffer(4));
    svg2pdfMock.mockResolvedValue(undefined);
  });

  it("throws without constructing a PDF document when there are no pages", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");

    await expect(assemblePdfFromSvgPages([])).rejects.toThrow("No pages to export.");
    expect(jsPDFConstructorMock).not.toHaveBeenCalled();
    expect(svg2pdfMock).not.toHaveBeenCalled();
  });

  it("single page, no pageSize argument: defaults to A4-portrait, never calls addPage", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    const page = fakeSvgPage("only", 210 * CSS_PX_PER_MM, 297 * CSS_PX_PER_MM);

    const bytes = await assemblePdfFromSvgPages([page]);

    expect(jsPDFConstructorMock).toHaveBeenCalledTimes(1);
    expect(jsPDFConstructorMock).toHaveBeenCalledWith({ orientation: "portrait", unit: "mm", format: [210, 297] });
    expect(addPageMock).not.toHaveBeenCalled();
    expect(svg2pdfMock).toHaveBeenCalledTimes(1);
    expect(svg2pdfMock).toHaveBeenCalledWith(page, expect.anything(), { x: 0, y: 0, width: 210, height: 297 });
    expect(outputMock).toHaveBeenCalledWith("arraybuffer");
    expect(bytes).toBeInstanceOf(Uint8Array);
  });

  it.each([
    ["A4", FORMATS.A4.pdfFormat],
    ["Letter", FORMATS.Letter.pdfFormat],
  ] as const)(
    "%s: jsPDF page format and svg2pdf width/height match exactly (no stretch mismatch)",
    async (pageSize, [width, height]) => {
      const { assemblePdfFromSvgPages } = await import("./exportPdf");
      const page = fakeSvgPage("only", width * CSS_PX_PER_MM, height * CSS_PX_PER_MM);

      await assemblePdfFromSvgPages([page], pageSize);

      // The jsPDF document itself was constructed at this format's exact
      // physical mm dimensions...
      expect(jsPDFConstructorMock).toHaveBeenCalledWith({ orientation: "portrait", unit: "mm", format: [width, height] });
      // ...and svg2pdf was told to render the page into a region of THE
      // SAME width/height, starting at the page origin. If these two ever
      // diverged, the SVG would be stretched to fill a region that isn't
      // the actual PDF page size -- distorting the engraving. Comparing
      // the two call sites directly (not just each against a literal)
      // is what catches that class of regression even if someone edits
      // one call site without the other.
      const [, , svg2pdfOptions] = svg2pdfMock.mock.calls[0] as [unknown, unknown, { width: number; height: number }];
      expect(svg2pdfOptions).toEqual({ x: 0, y: 0, width, height });
      const [jsPdfConstructorOptions] = jsPDFConstructorMock.mock.calls[0] as [{ format: [number, number] }];
      expect(jsPdfConstructorOptions.format).toEqual([svg2pdfOptions.width, svg2pdfOptions.height]);
      // Both dimensions carry the same page-format aspect ratio the fake
      // page's own (px) dimensions were built from -- i.e. nothing along
      // this path silently swaps in a different aspect ratio.
      expect(width / height).toBeCloseTo((page as unknown as { __widthPx: number }).__widthPx / (page as unknown as { __heightPx: number }).__heightPx, 5);
    },
  );

  it("Letter multi-page: addPage receives the Letter mm size, not A4's", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    const pages = [fakeSvgPage("p0", 1, 1), fakeSvgPage("p1", 1, 1)];

    await assemblePdfFromSvgPages(pages, "Letter");

    expect(addPageMock).toHaveBeenCalledTimes(1);
    expect(addPageMock).toHaveBeenCalledWith([215.9, 279.4], "portrait");
  });

  it("multi-page: calls addPage exactly once between each pair of consecutive pages, in order", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    const pages = [fakeSvgPage("p0", 1, 1), fakeSvgPage("p1", 1, 1), fakeSvgPage("p2", 1, 1)];

    const order: string[] = [];
    addPageMock.mockImplementation(() => order.push("addPage"));
    svg2pdfMock.mockImplementation(async (el: { __label: string }) => {
      order.push(`svg2pdf:${el.__label}`);
    });

    await assemblePdfFromSvgPages(pages);

    expect(addPageMock).toHaveBeenCalledTimes(2);
    expect(addPageMock).toHaveBeenCalledWith([210, 297], "portrait");
    expect(svg2pdfMock).toHaveBeenCalledTimes(3);
    // No addPage before the FIRST page (the constructor already created
    // it); exactly one addPage between each later page and the one before
    // it -- i.e. pages render onto the document in order.
    expect(order).toEqual([
      "svg2pdf:p0",
      "addPage",
      "svg2pdf:p1",
      "addPage",
      "svg2pdf:p2",
    ]);
  });

  it("wraps the ArrayBuffer jsPDF.output() returns in a Uint8Array of the same bytes", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    const sourceBytes = new Uint8Array([1, 2, 3, 4]);
    outputMock.mockReturnValue(sourceBytes.buffer);

    const bytes = await assemblePdfFromSvgPages([fakeSvgPage("only", 1, 1)]);

    expect(bytes).toEqual(sourceBytes);
  });

  it("propagates a svg2pdf failure without calling output()", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    svg2pdfMock.mockRejectedValueOnce(new Error("svg2pdf exploded"));

    await expect(assemblePdfFromSvgPages([fakeSvgPage("only", 1, 1)])).rejects.toThrow("svg2pdf exploded");
    expect(outputMock).not.toHaveBeenCalled();
  });

  // Bug 1 fix: the title is rendered as its own raster strip (never
  // through OSMD/svg2pdf.js -- see exportPdf.ts's buildTitleImage() doc
  // comment on why) and composited onto page 1 only. These tests cover
  // the PLACEMENT geometry in assemblePdfFromSvgPages() itself (pure
  // orchestration, same as the rest of this describe block) via a fake
  // TitleImage -- buildTitleImage()'s own real-canvas rendering is
  // DOM-only and covered separately by the wrapTitleLines() tests below
  // plus by hand in the real webview, same limitation as
  // renderScorePagesToSvg() (see this file's header comment).
  describe("assemblePdfFromSvgPages with a titleImage", () => {
    it("no titleImage (default null): behaves exactly as before, no addImage call", async () => {
      const { assemblePdfFromSvgPages } = await import("./exportPdf");
      const page = fakeSvgPage("only", 210 * CSS_PX_PER_MM, 297 * CSS_PX_PER_MM);

      await assemblePdfFromSvgPages([page]);

      expect(addImageMock).not.toHaveBeenCalled();
      expect(svg2pdfMock).toHaveBeenCalledWith(page, expect.anything(), { x: 0, y: 0, width: 210, height: 297 });
    });

    it("draws the title image full-width at the top of page 1, and scales page 1's music down (never stretched) to fit below it", async () => {
      const { assemblePdfFromSvgPages } = await import("./exportPdf");
      const page = fakeSvgPage("only", 210 * CSS_PX_PER_MM, 297 * CSS_PX_PER_MM);
      const titleImage = { dataUrl: "data:image/png;base64,AAAA", heightMm: 27 };

      await assemblePdfFromSvgPages([page], "A4", titleImage);

      expect(addImageMock).toHaveBeenCalledTimes(1);
      expect(addImageMock).toHaveBeenCalledWith(titleImage.dataUrl, "PNG", 0, 0, 210, 27);

      expect(svg2pdfMock).toHaveBeenCalledTimes(1);
      const [, , svg2pdfOptions] = svg2pdfMock.mock.calls[0] as [unknown, unknown, { x: number; y: number; width: number; height: number }];
      // Page starts exactly where the title image ends -- no gap, no
      // overlap.
      expect(svg2pdfOptions.y).toBe(27);
      expect(svg2pdfOptions.height).toBe(297 - 27);
      // Uniform scale-down: the box's own aspect ratio still matches the
      // full page's (210/297) -- i.e. this is a smaller copy of the same
      // shape, not a squish.
      expect(svg2pdfOptions.width / svg2pdfOptions.height).toBeCloseTo(210 / 297, 5);
      // Horizontally centered in the (now-narrower) page width.
      expect(svg2pdfOptions.x).toBeCloseTo((210 - svg2pdfOptions.width) / 2, 5);
    });

    it("only page 1 gets the title image and the scale-down -- later pages render full-size as usual", async () => {
      const { assemblePdfFromSvgPages } = await import("./exportPdf");
      const pages = [fakeSvgPage("p0", 1, 1), fakeSvgPage("p1", 1, 1)];
      const titleImage = { dataUrl: "data:image/png;base64,AAAA", heightMm: 20 };

      await assemblePdfFromSvgPages(pages, "A4", titleImage);

      expect(addImageMock).toHaveBeenCalledTimes(1);
      expect(svg2pdfMock).toHaveBeenCalledTimes(2);
      const [, , secondPageOptions] = svg2pdfMock.mock.calls[1] as [unknown, unknown, { x: number; y: number; width: number; height: number }];
      expect(secondPageOptions).toEqual({ x: 0, y: 0, width: 210, height: 297 });
    });
  });
});

// wrapTitleLines() -- pure text-layout math (Bug 1 fix's title raster
// text-wrapping, see exportPdf.ts's own doc comment on the split between
// this and the DOM-only buildTitleImage()). `measureWidth` here is a
// deterministic fake (1 unit per UTF-16 code unit) rather than a real
// canvas measurement -- exactly what makes this half unit-testable at all
// under this project's DOM-less `environment: "node"` vitest config.
describe("wrapTitleLines", () => {
  const measureWidth = (text: string): number => text.length;

  it("returns an empty array for blank/whitespace-only input", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    expect(wrapTitleLines(measureWidth, "", 100, 3)).toEqual([]);
    expect(wrapTitleLines(measureWidth, "   \n\t  ", 100, 3)).toEqual([]);
  });

  it("returns an empty array when maxLines is non-positive", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    expect(wrapTitleLines(measureWidth, "Fairy Tale", 100, 0)).toEqual([]);
    expect(wrapTitleLines(measureWidth, "Fairy Tale", 100, -1)).toEqual([]);
  });

  it("a short title that fits stays on a single line, trimmed", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    expect(wrapTitleLines(measureWidth, "  Fairy Tale  ", 100, 3)).toEqual(["Fairy Tale"]);
  });

  it("word-wraps Latin/Vietnamese text at word boundaries, never mid-word", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    // "aaa bbb" = 7 chars, exactly maxWidthPx; adding " ccc" would be 11.
    const lines = wrapTitleLines(measureWidth, "aaa bbb ccc ddd", 7, 10);
    expect(lines).toEqual(["aaa bbb", "ccc ddd"]);
    // No line carries a stray leading/trailing space from the collapse.
    for (const line of lines) {
      expect(line).toBe(line.trim());
    }
  });

  it("wraps CJK text between individual characters (no spaces to break on)", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    // 6 Han characters, maxWidthPx 3 -> 3 chars per line, 2 lines.
    const lines = wrapTitleLines(measureWidth, "起风了合唱版", 3, 10);
    expect(lines).toEqual(["起风了", "合唱版"]);
  });

  it("keeps a Vietnamese word whole while still wrapping adjacent CJK per-character", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    // "Xướng" (5 chars) must never split; "起风" (2 chars) can break from it.
    const lines = wrapTitleLines(measureWidth, "Xướng 起风", 6, 10);
    expect(lines).toEqual(["Xướng", "起风"]);
  });

  it("caps at maxLines and appends an ellipsis to the last kept line when content overflows", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    const lines = wrapTitleLines(measureWidth, "aaa bbb ccc ddd eee", 7, 2);
    expect(lines).toHaveLength(2);
    expect(lines[0]).toBe("aaa bbb");
    expect(lines[1].endsWith("…")).toBe(true);
    // The truncated line (ellipsis included) still respects maxWidthPx.
    expect(measureWidth(lines[1])).toBeLessThanOrEqual(7);
  });

  it("does not append an ellipsis when the content fits exactly within maxLines", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    const lines = wrapTitleLines(measureWidth, "aaa bbb ccc ddd", 7, 2);
    expect(lines).toEqual(["aaa bbb", "ccc ddd"]);
    expect(lines[1].endsWith("…")).toBe(false);
  });

  it("falls back to a bare ellipsis when maxWidthPx is too narrow even for one truncated character", async () => {
    const { wrapTitleLines } = await import("./exportPdf");
    const lines = wrapTitleLines(measureWidth, "aaaaaaaaaa bbbbbbbbbb", 1, 1);
    expect(lines).toEqual(["…"]);
  });
});
