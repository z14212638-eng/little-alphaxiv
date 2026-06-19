// Pure helpers for multi-source paper results: stable opaque-id resolution,
// the 3-way click-routing decision, and building the LLM tool list from the
// user's enabled search sources. All side-effect-free so they're unit-testable.

import type { Paper, ToolDef } from "../types";

/** Stable opaque id used as IDB key + route param + React key. arXiv id wins
 *  (it opens the existing /api/pdf path); else DOI; else a source-tagged stub. */
export function resolvePaperId(p: Paper): string {
  if (p.arxiv_id) return p.arxiv_id;
  if (p.doi) return `doi:${p.doi}`;
  return `${p.source ?? "paper"}:`;
}

export type OpenTarget =
  | { kind: "arxiv"; id: string }
  | { kind: "oa"; id: string; url: string }
  | { kind: "external"; url: string };

/** Decide what happens when the user clicks a paper card:
 *  - arXiv id present  -> open the existing in-app PDF preview
 *  - has an OA PDF URL -> open via the /api/pdf-url open proxy
 *  - otherwise         -> open the external landing page in a new tab */
export function openTarget(p: Paper): OpenTarget {
  if (p.arxiv_id) return { kind: "arxiv", id: p.arxiv_id };
  if (p.oa_pdf_url) return { kind: "oa", id: resolvePaperId(p), url: p.oa_pdf_url };
  return { kind: "external", url: p.external_url || "" };
}

/** Build the LLM tool list for the current turn. arXiv is always present;
 *  OpenAlex / Semantic Scholar tools appear only when the user enabled them. */
export function buildSearchTools(sources: {
  openalex: boolean;
  s2: boolean;
}): ToolDef[] {
  const tools: ToolDef[] = [
    {
      type: "function",
      function: {
        name: "search_arxiv",
        description:
          "Search arXiv for academic preprints by keyword, topic, or author. " +
          "Returns matching papers with title, authors, abstract, and a clickable link to preview the PDF in-app. " +
          "Always available. Use this when the user wants preprints / arXiv papers.",
        parameters: {
          type: "object",
          properties: {
            query: { type: "string", description: "Search terms for arXiv. Concise keywords work best." },
            max_results: { type: "number", description: "Max papers to return (default 8)." },
          },
          required: ["query"],
        },
      },
    },
  ];

  if (sources.openalex) {
    tools.push({
      type: "function",
      function: {
        name: "search_openalex",
        description:
          "Search OpenAlex, a broad open catalog of scholarly works across all fields " +
          "(journals, conferences, preprints — not just arXiv). Best for published, peer-reviewed literature " +
          "and broader coverage than arXiv. Open-access results can be previewed in-app; others open externally.",
        parameters: {
          type: "object",
          properties: {
            query: { type: "string", description: "Search terms for OpenAlex." },
            max_results: { type: "number", description: "Max papers to return (default 8)." },
          },
          required: ["query"],
        },
      },
    });
  }

  if (sources.s2) {
    tools.push({
      type: "function",
      function: {
        name: "search_semantic_scholar",
        description:
          "Search Semantic Scholar's academic graph (214M papers across all fields). " +
          "Good for citation-rich discovery and works indexed from many publishers. " +
          "Open-access results can be previewed in-app; others open externally.",
        parameters: {
          type: "object",
          properties: {
            query: { type: "string", description: "Search terms for Semantic Scholar." },
            max_results: { type: "number", description: "Max papers to return (default 8)." },
          },
          required: ["query"],
        },
      },
    });
  }

  tools.push({
    type: "function",
    function: {
      name: "web_search",
      description:
        "General web search (via anysearch) for non-academic information: " +
        "recent news, blog posts, people, products, or anything not an academic paper. " +
        "Use the paper-search tools (search_arxiv / search_openalex / search_semantic_scholar) for finding papers.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Web search query." },
        },
        required: ["query"],
      },
    },
  });

  return tools;
}
