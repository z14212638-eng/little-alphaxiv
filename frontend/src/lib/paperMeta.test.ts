import { describe, it, expect } from "vitest";
import { hasRealTitle, paperThreadTitle } from "./paperMeta";

describe("hasRealTitle", () => {
  it("is true for a non-empty title that differs from the id", () => {
    expect(hasRealTitle({ title: "Attention Is All You Need" }, "sha256:179abc")).toBe(true);
  });
  it("is false when the title equals the bare id (a stub)", () => {
    expect(hasRealTitle({ title: "2401.12345" }, "2401.12345")).toBe(false);
  });
  it("is false for an empty / whitespace-only title", () => {
    expect(hasRealTitle({ title: "" }, "sha256:179abc")).toBe(false);
    expect(hasRealTitle({ title: "   " }, "sha256:179abc")).toBe(false);
  });
  it("is false when the record is null/undefined", () => {
    expect(hasRealTitle(null, "sha256:179abc")).toBe(false);
    expect(hasRealTitle(undefined, "sha256:179abc")).toBe(false);
  });
});

describe("paperThreadTitle", () => {
  it("returns the paper's real title when known (the upload case)", () => {
    // A locally-uploaded PDF with no DOI has id `sha256:<hash>` but a real
    // title in the cache. The thread must show the title, not `📄 sha256:…`.
    expect(paperThreadTitle({ title: "Attention Is All You Need" }, "sha256:179abcdef")).toBe(
      "Attention Is All You Need",
    );
  });
  it("falls back to `📄 <id>` when the title equals the id (arXiv stub)", () => {
    expect(paperThreadTitle({ title: "2401.12345" }, "2401.12345")).toBe("📄 2401.12345");
  });
  it("falls back to `📄 <id>` when there is no cached title", () => {
    expect(paperThreadTitle({ title: "" }, "sha256:179abcdef")).toBe("📄 sha256:179abcdef");
    expect(paperThreadTitle(null, "sha256:179abcdef")).toBe("📄 sha256:179abcdef");
    expect(paperThreadTitle(undefined, "sha256:179abcdef")).toBe("📄 sha256:179abcdef");
  });
  it("trims surrounding whitespace from the title", () => {
    expect(paperThreadTitle({ title: "  My Paper  " }, "sha256:x")).toBe("My Paper");
  });
  it("treats the backend's `Untitled upload` fallback as a real title", () => {
    // paper_uploads.py stores `title or "Untitled upload"`; that's still less
    // confusing than a raw `sha256:` id in the sidebar.
    expect(paperThreadTitle({ title: "Untitled upload" }, "sha256:179abcdef")).toBe(
      "Untitled upload",
    );
  });
});
