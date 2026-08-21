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
 * identity/order in assertions below. */
function fakeSvgPage(label: string): SVGElement {
  return { __label: label } as unknown as SVGElement;
}

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

  it("single page: constructs one A4-portrait document, renders it, never calls addPage", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    const page = fakeSvgPage("only");

    const bytes = await assemblePdfFromSvgPages([page]);

    expect(jsPDFConstructorMock).toHaveBeenCalledTimes(1);
    expect(jsPDFConstructorMock).toHaveBeenCalledWith({ orientation: "portrait", unit: "mm", format: [210, 297] });
    expect(addPageMock).not.toHaveBeenCalled();
    expect(svg2pdfMock).toHaveBeenCalledTimes(1);
    expect(svg2pdfMock).toHaveBeenCalledWith(page, expect.anything(), { x: 0, y: 0, width: 210, height: 297 });
    expect(outputMock).toHaveBeenCalledWith("arraybuffer");
    expect(bytes).toBeInstanceOf(Uint8Array);
  });

  it("multi-page: calls addPage exactly once between each pair of consecutive pages, in order", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    const pages = [fakeSvgPage("p0"), fakeSvgPage("p1"), fakeSvgPage("p2")];

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

    const bytes = await assemblePdfFromSvgPages([fakeSvgPage("only")]);

    expect(bytes).toEqual(sourceBytes);
  });

  it("propagates a svg2pdf failure without calling output()", async () => {
    const { assemblePdfFromSvgPages } = await import("./exportPdf");
    svg2pdfMock.mockRejectedValueOnce(new Error("svg2pdf exploded"));

    await expect(assemblePdfFromSvgPages([fakeSvgPage("only")])).rejects.toThrow("svg2pdf exploded");
    expect(outputMock).not.toHaveBeenCalled();
  });
});
