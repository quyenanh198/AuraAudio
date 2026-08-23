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

vi.mock("jspdf", () => ({
  jsPDF: class {
    constructor(options: unknown) {
      jsPDFConstructorMock(options);
    }

    addPage(...args: unknown[]): void {
      addPageMock(...args);
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
});
