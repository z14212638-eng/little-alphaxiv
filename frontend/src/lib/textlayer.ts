// Thin wrapper around pdf.js TextLayer for selectable text.
// pdf.js v4 exposes the TextLayer class on the main export.

import * as pdfjsLib from "pdfjs-dist";

type TextContentSource =
  | { items: any[]; styles?: Record<string, unknown> }
  | Promise<{ items: any[]; styles?: Record<string, unknown> }>;

export interface TextLayerRenderTask {
  promise: Promise<void>;
  cancel: () => void;
}

/**
 * Render a text layer for selection. Mirrors pdf.js v4 TextLayer.render signature.
 * Falls back gracefully if the API shape differs across versions.
 */
export function renderTextLayer(opts: {
  textContentSource: TextContentSource;
  container: HTMLElement;
  viewport: pdfjsLib.PageViewport;
}): TextLayerRenderTask {
  const anyLib = pdfjsLib as any;
  // pdf.js v4: new pdfjsLib.TextLayer({ textContentSource, container, viewport })
  const TL = anyLib.TextLayer;
  if (typeof TL === "function") {
    const instance = new TL({
      textContentSource: opts.textContentSource,
      container: opts.container,
      viewport: opts.viewport,
    });
    const p = instance.render();
    return {
      promise: Promise.resolve(p),
      cancel: () => {
        try {
          instance.cancel();
        } catch {
          /* ignore */
        }
      },
    };
  }
  // fallback: legacy renderTextLayer function
  if (typeof anyLib.renderTextLayer === "function") {
    const task = anyLib.renderTextLayer({
      textContentSource: opts.textContentSource,
      container: opts.container,
      viewport: opts.viewport,
    });
    return {
      promise: task.promise,
      cancel: () => task.cancel(),
    };
  }
  // no text layer available — return a no-op
  return { promise: Promise.resolve(), cancel: () => {} };
}
