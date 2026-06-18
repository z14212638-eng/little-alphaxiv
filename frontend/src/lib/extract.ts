// PDF text extraction in the browser via pdf.js getTextContent().
// Extracted once per paper, cached in IndexedDB. The text is injected into
// the per-paper chat context so the assistant can discuss the paper's content.

import * as pdfjsLib from "pdfjs-dist";
// Vite-friendly worker import.
import PdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { pdfUrl } from "./api";

pdfjsLib.GlobalWorkerOptions.workerSrc = PdfWorker;

/** Load a PDF document (via the backend proxy URL) and extract its full text. */
export async function extractPaperText(arxivId: string): Promise<string> {
  const loadingTask = pdfjsLib.getDocument({
    url: pdfUrl(arxivId),
    // disable worker fetching of fonts etc. we just need text
    disableFontFace: true,
  });
  const doc = await loadingTask.promise;

  const pages: string[] = [];
  for (let i = 1; i <= doc.numPages; i++) {
    const page = await doc.getPage(i);
    const content = await page.getTextContent();
    // Reconstruct lines: group items by their transform y, join x-sorted items.
    const lines = groupIntoLines(content.items);
    pages.push(`--- Page ${i} ---\n${lines.join("\n")}`);
    page.cleanup();
  }
  await doc.destroy();
  return pages.join("\n\n");
}

interface TextItemLike {
  str: string;
  transform: number[];
  width?: number;
  hasEOL?: boolean;
}

function groupIntoLines(items: any[]): string[] {
  const lines: string[] = [];
  let current: string[] = [];
  let lastY: number | null = null;
  for (const it of items as TextItemLike[]) {
    const y = it.transform?.[5] ?? 0;
    if (lastY !== null && Math.abs(y - lastY) > 3) {
      // new line
      if (current.length) lines.push(current.join(""));
      current = [];
    }
    if (it.str) current.push(it.str);
    if (it.hasEOL) {
      if (current.length) lines.push(current.join(""));
      current = [];
      lastY = null;
      continue;
    }
    lastY = y;
  }
  if (current.length) lines.push(current.join(""));
  return lines;
}
