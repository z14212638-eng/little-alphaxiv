import { describe, it, expect } from "vitest";
import { resolvePaperId, openTarget, buildSearchTools } from "./paperSource";
import type { Paper } from "../types";

function p(over: Partial<Paper> = {}): Paper {
  return {
    arxiv_id: "",
    title: "",
    authors: [],
    abstract: "",
    pdf_url: "",
    abs_url: "",
    published: "",
    primary_category: "",
    ...over,
  };
}

describe("resolvePaperId", () => {
  it("uses the bare arXiv id when present", () => {
    expect(resolvePaperId(p({ arxiv_id: "2401.12345" }))).toBe("2401.12345");
  });
  it("falls back to doi:<doi> when no arXiv id but a DOI exists", () => {
    expect(resolvePaperId(p({ arxiv_id: "", doi: "10.1000/xyz" }))).toBe("doi:10.1000/xyz");
  });
  it("falls back to <source>:<arxiv_id-shape> otherwise", () => {
    expect(resolvePaperId(p({ arxiv_id: "", source: "s2", doi: "" }))).toBe("s2:");
  });
});

describe("openTarget", () => {
  it("routes arXiv-id papers to the arXiv in-app path", () => {
    expect(openTarget(p({ arxiv_id: "2401.12345" }))).toEqual({ kind: "arxiv", id: "2401.12345" });
  });
  it("routes non-arXiv OA papers to the OA proxy path", () => {
    const r = openTarget(p({ arxiv_id: "", doi: "10.1000/xyz", oa_pdf_url: "https://example.org/a.pdf", source: "openalex" }));
    expect(r.kind).toBe("oa");
    if (r.kind === "oa") {
      expect(r.id).toBe("doi:10.1000/xyz");
      expect(r.url).toBe("https://example.org/a.pdf");
    }
  });
  it("routes papers with neither arXiv id nor OA to external_url", () => {
    const r = openTarget(p({ arxiv_id: "", doi: "10.1000/xyz", external_url: "https://doi.org/10.1000/xyz", source: "s2" }));
    expect(r).toEqual({ kind: "external", url: "https://doi.org/10.1000/xyz" });
  });
});

describe("buildSearchTools", () => {
  it("returns only arXiv + web_search when nothing enabled", () => {
    const names = buildSearchTools({ openalex: false, s2: false }).map((t) => t.function.name);
    expect(names).toEqual(["search_arxiv", "web_search"]);
  });
  it("includes search_openalex when openalex enabled", () => {
    const names = buildSearchTools({ openalex: true, s2: false }).map((t) => t.function.name);
    expect(names).toEqual(["search_arxiv", "search_openalex", "web_search"]);
  });
  it("includes search_semantic_scholar when s2 enabled", () => {
    const names = buildSearchTools({ openalex: false, s2: true }).map((t) => t.function.name);
    expect(names).toEqual(["search_arxiv", "search_semantic_scholar", "web_search"]);
  });
  it("includes all three sources when both enabled", () => {
    const names = buildSearchTools({ openalex: true, s2: true }).map((t) => t.function.name);
    expect(names).toEqual(["search_arxiv", "search_openalex", "search_semantic_scholar", "web_search"]);
  });
});
