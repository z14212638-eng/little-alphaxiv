# Task 8 Report: Wire buildSearchTools + openalex/s2 dispatch + fallback

## Status: COMPLETE

**Commit:** `baba16d` (baba16d81a19195b90757f2e3293caa2ac7148c0)
**Summary:** `feat(search): wire buildSearchTools + openalex/s2 dispatch + fallback`

---

## Files Changed

### `frontend/src/lib/llm.ts` (+27 / -52 net lines)

**Imports updated:**
- Added `searchOpenAlex`, `searchSemanticScholar` to the `./api` import
- Removed `ToolDef` from type import (no longer needed)
- Added `import { buildSearchTools } from "./paperSource"`

**SEARCH_TOOLS removed:**
- Deleted the `export const SEARCH_TOOLS: ToolDef[] = [...]` constant (44 lines)
- Replaced `tools: SEARCH_TOOLS,` with `tools,` in the streamChat call
- Confirmed zero remaining references via grep (no other importer)

**runConversation signature extended:**
```ts
enabledSources?: { openalex: boolean; s2: boolean };
searchSourceCreds?: { openalex: { apiKey: string; email: string }; semanticScholar: { apiKey: string } };
```
- Both destructured from opts alongside existing fields
- `const tools = buildSearchTools(enabledSources ?? { openalex: false, s2: false });` builds the tool list dynamically

**Two new dispatch branches added** (between search_arxiv and web_search):

1. **search_openalex**:
   - Sets status "Searching OpenAlex..."
   - Calls `searchOpenAlex(query, maxResults, creds)` with try/catch
   - On success: sets ui:{papers}, calls onPapers, pushes tool message to both arrays
   - On error: pushes fallback message `"openalex search failed (...); try search_arxiv"` so the model falls back to arxiv

2. **search_semantic_scholar**:
   - Same pattern as openalex
   - Calls `searchSemanticScholar(query, maxResults, apiKey)`
   - Fallback message: `"semantic scholar search failed (...); try search_arxiv"`

**Title-gen label generalized:**
- Changed "arxiv id:" to "paper id:" (field name arxivId preserved)

### `frontend/src/components/ChatPanel.tsx` (+4 lines net)

**New store selectors** (after provider selector):
```ts
const searchSources = useSettings((s) => s.searchSources);
const enabledSources = { openalex: searchSources.openalex.enabled, s2: searchSources.semanticScholar.enabled };
```

**Zustand footgun AVOIDED:** Selected stable `searchSources` object; derived booleans locally outside selector.

**runConversation call extended** with `enabledSources` and `searchSourceCreds`.

---

## Typecheck Output

```
> tsc --noEmit
(no errors) -- clean pass
```

## Vitest Summary

```
 Test Files  5 passed (5)
      Tests  63 passed (63)
   Duration  818ms
```
All green. Zero regressions. paperSource tests (Task 7) still pass.

---

## Self-Review

| Check | Status |
|-------|--------|
| Zustand footgun avoided | PASS |
| Fallback messages present | PASS |
| SEARCH_TOOLS removed | PASS (0 occurrences) |
| Title label generalized | PASS |
| No test regression | PASS (63/63) |
| Typecheck clean | PASS |
| maybeSummarizeTitle untouched | PASS |
