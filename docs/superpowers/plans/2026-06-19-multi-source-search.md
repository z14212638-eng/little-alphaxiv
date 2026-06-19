# Multi-Source Search (OpenAlex + Semantic Scholar) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add OpenAlex and Semantic Scholar as optional academic search sources alongside arXiv, configured in Settings; non-arXiv open-access results open in the existing PaperView via an extended PDF proxy; arXiv stays the always-on default fallback.

**Architecture:** Each source is an independent LLM tool (only present when enabled) following the existing `search_arxiv` pattern. Two new backend search routers normalize each source's payload to the shared `Paper` shape. A new `/api/pdf-url` open-proxy route (SSRF-guarded, hashed cache) lets the existing PaperView/PdfViewer render any OA PDF. The `arxiv_id` field stays the opaque key throughout (no IDB migration); optional `source`/`doi`/`oa_pdf_url`/`external_url` fields extend `Paper`. Settings gains a `searchSources` slice that auto-persists.

**Tech Stack:** FastAPI + httpx (backend), React + zustand + TypeScript (frontend), Vitest (unit tests), Playwright Python (E2E). No new dependencies — httpx already used in `pdf.py`/`search.py`.

## Global Constraints

- **Type gate is the only frontend gate:** `npm run typecheck` (`tsc --noEmit`). There is no lint script. Every frontend task must end with typecheck passing.
- **No backend test runner:** the repo has no pytest. Backend pure functions are verified via standalone `tools/verify_*.py` scripts run with the `Agent_env` Python; the project convention (see `tools/verify_bold.py`) is `sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")` + a `VERDICT: PASS/FAIL` line + `sys.exit(0/1)`.
- **Python env:** all `python` runs use the `Agent_env` conda env. Per global CLAUDE.md, activate it: `conda activate Agent_env` (or use the absolute interpreter `/c/Users/Delig/.conda/envs/Agent_env/python.exe`). Backend deps install via `pip install -r requirements.txt`.
- **Vitest style:** `import { describe, it, expect } from "vitest"`; one `describe` block per function. Files live next to the module as `*.test.ts`. Run a single file: `npx vitest run src/lib/foo.test.ts`.
- **Mock-LLM title-sniffing contract:** `tools/mock_llm.py` sniffs the system prompt for `"title generator"` and `"paper being discussed"`. Do not remove those phrases from `llm.ts` / `ChatPanel.tsx`.
- **Worktree workflow:** per project CLAUDE.md, all feature work happens in a fresh worktree under `.claude/worktrees/`; `frontend/node_modules` is a junction to the main repo's. Merge back to `main` when green; delete the worktree (remove the node_modules junction first).
- **React.StrictMode is disabled** — do not re-enable.
- **Paper key naming:** the IDB field stays `arxiv_id` (opaque string). Do NOT rename to `paper_id` — that is out of scope (Approach B, deferred).
- **Do not touch** `backend/app/routers/search.py` (arXiv path unchanged), `websearch.py`, or any annotation/title logic beyond the cosmetic preamble strings.
- arXiv always-on: even with both sources enabled, `search_arxiv` is always in the tool list.

---

## File Structure

**Backend (new routers + extend pdf):**
- Create `backend/app/routers/openalex.py` — `GET /api/openalex` search proxy → normalized `Paper[]`.
- Create `backend/app/routers/semantic_scholar.py` — `GET /api/semantic_scholar` search proxy → normalized `Paper[]`.
- Create `backend/app/routers/_papershared.py` — pure helpers shared by both: `resolve_arxiv_id_from_doi`, OpenAlex inverted-index→text, SSRF guard. (Pure so a `tools/verify_*.py` can import + test them without a server.)
- Modify `backend/app/routers/pdf.py` — add `GET /api/pdf-url?url=` open-proxy route + hashed cache key + SSRF guard (reuses existing `_serve_bytes`).
- Modify `backend/app/main.py` — register `openalex` + `semantic_scholar` routers.

**Frontend types + store:**
- Modify `frontend/src/types.ts` — extend `Paper` with optional `source`/`doi`/`oa_pdf_url`/`external_url`.
- Modify `frontend/src/store/settings.ts` — add `searchSources` state + setters (auto-persisted).

**Frontend lib:**
- Modify `frontend/src/lib/api.ts` — add `searchOpenAlex`, `searchSemanticScholar`, `pdfUrlForOa`; add `resolvePaperId` + `openTargetFor` pure helpers.
- Create `frontend/src/lib/paperSource.test.ts` — Vitest for `resolvePaperId` + `openTargetFor` + `buildSearchTools`.
- Modify `frontend/src/lib/llm.ts` — `buildSearchTools(sources)` + new dispatch branches + generalize title label.

**Frontend components/views:**
- Modify `frontend/src/components/PaperCard.tsx` — source badge + dynamic CTA.
- Modify `frontend/src/components/ChatPanel.tsx` — `onOpenPaper` takes `Paper`, seeds metadata, 3-way open routing.
- Modify `frontend/src/views/ChatView.tsx` — dynamic `GENERAL_SYSTEM_PROMPT`.
- Modify `frontend/src/views/PaperView.tsx` — generalize preamble; pass `oa_pdf_url` into PdfViewer.
- Modify `frontend/src/components/PdfViewer.tsx` — accept optional `pdfUrlOverride`, seed metadata on load.
- Modify `frontend/src/views/SettingsView.tsx` — add "Search sources" section.

**E2E:**
- Modify `tools/mock_llm.py` — recognize `search_openalex` / `search_semantic_scholar` tool calls (canned papers incl. one OA + one external-only).
- Create `tools/drive_multisource.py` — Playwright driver intercepting the new endpoints, asserting badge + OA-open + external-open.

---

## Task 1: Backend shared pure helpers + verify script

**Files:**
- Create: `backend/app/routers/_papershared.py`
- Test: `tools/verify_papershared.py`

**Interfaces:**
- Produces:
  - `normalize_doi(raw: str | None) -> str` — lowercases, strips leading `https://doi.org/` / `doi:` / `DOI:`. Returns `""` if empty/None.
  - `arxiv_id_from_doi(doi: str) -> str | None` — if `doi` starts with `10.48550/arxiv.` (case-insensitive), returns the bare arXiv id (the segment after `arxiv.`); else `None`.
  - `abstract_from_inverted_index(inv: dict | None) -> str` — OpenAlex `abstract_inverted_index` `{word: [positions]}` → reconstructed string; `""` for falsy/empty.
  - `is_safe_external_url(url: str) -> tuple[bool, str]` — returns `(ok, reason)`. `ok=False` for non-http(s), private/loopback/link-local hosts (resolved via `socket.getaddrinfo`), or parse failure. `ok=True` otherwise.
- Consumes: nothing (pure).

- [ ] **Step 1: Write the verify script (the test, since there's no pytest)**

Create `tools/verify_papershared.py`:

```python
"""Verify the pure helpers in backend/app/routers/_papershared.py.

No server needed — imports the module directly and asserts behavior. Run with
the Agent_env interpreter:

    conda activate Agent_env
    python tools/verify_papershared.py
"""
from __future__ import annotations

import codecs
import sys
from pathlib import Path

sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
sys.stderr = codecs.getwriter("utf-8")(sys.stderr.buffer, errors="replace")

# Make backend importable without running the server.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.app.routers._papershared import (  # noqa: E402
    normalize_doi,
    arxiv_id_from_doi,
    abstract_from_inverted_index,
    is_safe_external_url,
)

errors: list[str] = []


def check(label: str, got, want):
    ok = got == want
    print(f"{'PASS' if ok else 'FAIL'} {label}: got={got!r} want={want!r}")
    if not ok:
        errors.append(label)


# --- normalize_doi ---
check("normalize_doi url", normalize_doi("https://doi.org/10.48550/arXiv.2401.12345"), "10.48550/arxiv.2401.12345")
check("normalize_doi prefix", normalize_doi("DOI: 10.1000/xyz"), "10.1000/xyz")
check("normalize_doi none", normalize_doi(None), "")
check("normalize_doi empty", normalize_doi(""), "")

# --- arxiv_id_from_doi ---
check("arxiv_doi new", arxiv_id_from_doi("10.48550/arxiv.2401.12345"), "2401.12345")
check("arxiv_doi case", arxiv_id_from_doi("10.48550/ARXIV.2401.12345"), "2401.12345")
check("arxiv_doi nonarxiv", arxiv_id_from_doi("10.1000/xyz"), None)
check("arxiv_doi empty", arxiv_id_from_doi(""), None)

# --- abstract_from_inverted_index ---
inv = {"hello": [0], "world": [1], "pdf": [3]}
check("inverted_index basic", abstract_from_inverted_index(inv), "hello world pdf")
check("inverted_index none", abstract_from_inverted_index(None), "")
check("inverted_index empty", abstract_from_inverted_index({}), "")

# --- is_safe_external_url ---
check("safe https", is_safe_external_url("https://arxiv.org/pdf/2401.12345")[0], True)
check("unsafe http-not-scheme", is_safe_external_url("ftp://example.org/x")[0], False)
check("unsafe no-scheme", is_safe_external_url("example.org/x")[0], False)
check("unsafe loopback", is_safe_external_url("http://127.0.0.1/x")[0], False)
check("unsafe private", is_safe_external_url("http://10.0.0.1/x")[0], False)
check("unsafe empty", is_safe_external_url("")[0], False)

print(f"\nVERDICT: {'PASS' if not errors else 'FAIL'} ({len(errors)} failures)")
sys.exit(0 if not errors else 1)
```

- [ ] **Step 2: Run the verify script to confirm it fails**

Run: `conda activate Agent_env && python tools/verify_papershared.py`
Expected: FAIL / ImportError (`No module named 'backend.app.routers._papershared'`).

- [ ] **Step 3: Write the implementation**

Create `backend/app/routers/_papershared.py`:

```python
"""Pure helpers shared by the OpenAlex and Semantic Scholar search routers.

Kept side-effect-free and import-light so tools/verify_papershared.py can unit
test them without standing up the FastAPI app.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def normalize_doi(raw: str | None) -> str:
    """Lowercase a DOI and strip any URL/prefix wrapper. '' for falsy."""
    if not raw:
        return ""
    d = raw.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:", "doi "):
        if d.startswith(prefix):
            d = d[len(prefix):]
            break
    return d.strip()


def arxiv_id_from_doi(doi: str | None) -> str | None:
    """OpenAlex indexes arXiv preprints with DOI prefix 10.48550/arxiv.<id>.
    Return the bare arXiv id, or None if this DOI isn't an arXiv preprint."""
    d = normalize_doi(doi)
    if not d.startswith("10.48550/arxiv."):
        return None
    tail = d[len("10.48550/arxiv."):]
    return tail or None


def abstract_from_inverted_index(inv: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}. Reconstruct the text
    in position order. '' for falsy/empty input."""
    if not inv:
        return ""
    max_pos = 0
    for positions in inv.values():
        for p in positions:
            if p > max_pos:
                max_pos = p
    words: list[str] = [""] * (max_pos + 1)
    for word, positions in inv.items():
        for p in positions:
            if 0 <= p <= max_pos:
                words[p] = word
    return " ".join(words).strip()


def is_safe_external_url(url: str) -> tuple[bool, str]:
    """SSRF guard for the open PDF proxy. Returns (ok, reason).
    Rejects non-http(s) schemes, unparseable URLs, and hosts that resolve to
    private / loopback / link-local / multicast IPs."""
    if not url:
        return False, "empty url"
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        return False, f"unparseable: {exc}"
    if parsed.scheme not in ("http", "https"):
        return False, f"unsupported scheme: {parsed.scheme}"
    host = parsed.hostname
    if not host:
        return False, "no host"
    # Resolve and check every returned address — one unsafe IP fails the URL.
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        return False, f"dns resolution failed: {exc}"
    for info in infos:
        ip_str = info[4][0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return False, f"host resolves to non-public ip: {ip}"
    return True, "ok"
```

- [ ] **Step 4: Run the verify script to confirm it passes**

Run: `conda activate Agent_env && python tools/verify_papershared.py`
Expected: all `PASS`, final `VERDICT: PASS (0 failures)`, exit 0.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/_papershared.py tools/verify_papershared.py
git commit -m "feat(search): shared pure helpers for multi-source paper search"
```

---

## Task 2: Semantic Scholar backend router

**Files:**
- Create: `backend/app/routers/semantic_scholar.py`
- Modify: `backend/app/main.py:19,37-38`

**Interfaces:**
- Produces: `router` (FastAPI APIRouter) with `GET /semantic_scholar` returning `JSONResponse({"total": int, "results": list[Paper]})`. Each result `Paper` dict has keys: `arxiv_id, title, authors, abstract, pdf_url, abs_url, published, primary_category, source, doi, oa_pdf_url, external_url`.
- Consumes: `_papershared.normalize_doi`, `arxiv_id_from_doi` (Task 1).
- Request query params: `q` (required), `max_results` (1-50, default 10), `api_key` (optional, passed as `x-api-key` header to S2).

- [ ] **Step 1: Write the implementation**

Create `backend/app/routers/semantic_scholar.py`:

```python
"""Semantic Scholar Graph API search proxy.

S2 sends no CORS headers, so the browser can't reach it directly. We query the
public /graph/v1/paper/search endpoint and normalize results to the shared
Paper shape. An optional API key (1 RPS, free — request at
semanticscholar.org/product/api#api-key) raises rate limits and is forwarded
as the x-api-key header.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ._papershared import normalize_doi, arxiv_id_from_doi

router = APIRouter()

_S2_SEARCH = "https://api.semanticscholar.org/graph/v1/paper/search"
_S2_FIELDS = "title,abstract,authors,year,externalIds,openAccessPdf,url"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def _parse_paper(item: dict[str, Any]) -> dict[str, Any]:
    ext = item.get("externalIds") or {}
    doi = normalize_doi(ext.get("DOI"))
    arxiv_id = ext.get("ArXiv") or arxiv_id_from_doi(doi) or ""
    arxiv_id = str(arxiv_id)

    authors: list[str] = []
    for a in item.get("authors") or []:
        name = a.get("name")
        if name:
            authors.append(name)

    oa = item.get("openAccessPdf") or {}
    oa_pdf_url = oa.get("url") or ""

    year = item.get("year")
    published = f"{year}-01-01" if year else ""

    # Landing page on semanticscholar.org (always available as external link).
    paper_id = item.get("paperId") or ""
    external_url = item.get("url") or (f"https://www.semanticscholar.org/paper/{paper_id}" if paper_id else "")

    return {
        "arxiv_id": arxiv_id,
        "title": item.get("title") or "",
        "authors": authors,
        "abstract": item.get("abstract") or "",
        # pdf_url/abs_url are arXiv-shaped; for S2 results they stay empty
        # (the OA path uses oa_pdf_url; non-OA uses external_url).
        "pdf_url": "",
        "abs_url": "",
        "published": published,
        "primary_category": "",
        "source": "s2",
        "doi": doi,
        "oa_pdf_url": oa_pdf_url,
        "external_url": external_url,
    }


@router.get("/semantic_scholar")
async def search_semantic_scholar(
    q: str = Query(..., description="Semantic Scholar search query"),
    max_results: int = Query(10, ge=1, le=50),
    api_key: str = Query("", description="Optional Semantic Scholar API key"),
) -> Any:
    headers = {"User-Agent": "little-alphaxiv/0.1"}
    if api_key:
        headers["x-api-key"] = api_key
    params = {
        "query": q,
        "limit": max_results,
        "fields": _S2_FIELDS,
    }
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(_S2_SEARCH, params=params, headers=headers)
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"semantic scholar request error: {exc}") from exc

    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="semantic scholar rate limited; try search_arxiv")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"semantic scholar returned status {resp.status_code}: {resp.text[:300]}",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"semantic scholar json error: {exc}") from exc

    items = data.get("data") or []
    results = [_parse_paper(it) for it in items]
    total = data.get("total", len(results))
    return JSONResponse(content={"total": total, "results": results})
```

- [ ] **Step 2: Register the router**

In `backend/app/main.py`, change the import line (line 19) and add the include. Edit the import:

```python
from .routers import llm, search, pdf, websearch, models, openalex, semantic_scholar
```

And add after the `models` include line (after line 38):

```python
app.include_router(openalex.router, prefix="/api")
app.include_router(semantic_scholar.router, prefix="/api")
```

- [ ] **Step 3: Confirm the app still imports**

Run: `conda activate Agent_env && python -c "from backend.app.main import app; print(sorted(r.path for r in app.routes))"`
Expected: the printed list includes `/api/semantic_scholar` and (after Task 3) `/api/openalex`. For now `/api/semantic_scholar` must appear and no `ImportError` — BUT this will fail to import because `openalex` does not exist yet.

> Note: because `main.py` imports `openalex` too, this import check only passes after Task 3. Skip the import check here; do it at the end of Task 3. Instead, do a syntax-only check now:
> Run: `conda activate Agent_env && python -c "import ast; ast.parse(open('backend/app/routers/semantic_scholar.py').read()); print('ok')"`
> Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/semantic_scholar.py backend/app/main.py
git commit -m "feat(search): Semantic Scholar search proxy router"
```

---

## Task 3: OpenAlex backend router

**Files:**
- Create: `backend/app/routers/openalex.py`

**Interfaces:**
- Produces: `router` with `GET /openalex` → `JSONResponse({"total": int, "results": list[Paper]})`, same `Paper` key set as Task 2, `source: "openalex"`.
- Consumes: `_papershared.normalize_doi`, `arxiv_id_from_doi`, `abstract_from_inverted_index`, `is_safe_external_url` (Task 1).
- Request query params: `q` (required), `max_results` (1-50, default 10), `api_key` (optional, `api_key` param to OpenAlex), `email` (optional, `mailto` polite-pool param).

- [ ] **Step 1: Write the implementation**

Create `backend/app/routers/openalex.py`:

```python
"""OpenAlex API search proxy.

OpenAlex sends no CORS headers, so the browser can't reach it directly. We
query the public /works endpoint and normalize results to the shared Paper
shape. An optional API key (free — get yours at openalex.org/settings/api)
and an optional email (the 'polite pool' mailto param) improve rate limits.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from ._papershared import normalize_doi, arxiv_id_from_doi, abstract_from_inverted_index

router = APIRouter()

_OPENALEX_WORKS = "https://api.openalex.org/works"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


def _parse_work(item: dict[str, Any]) -> dict[str, Any]:
    doi = normalize_doi(item.get("doi"))
    arxiv_id = arxiv_id_from_doi(doi) or ""

    authors: list[str] = []
    for a in item.get("authorships") or []:
        author = a.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(name)

    abstract = abstract_from_inverted_index(item.get("abstract_inverted_index"))

    # OpenAlex exposes the best OA PDF under best_oa_location.pdf_url.
    oa = item.get("best_oa_location") or item.get("open_access") or {}
    oa_pdf_url = ""
    if isinstance(oa, dict):
        oa_pdf_url = oa.get("pdf_url") or ""

    published = item.get("publication_date") or ""

    primary_topic = item.get("primary_topic") or {}
    primary_category = ""
    if isinstance(primary_topic, dict):
        primary_category = primary_topic.get("display_name") or ""

    # OpenAlex work id is like https://openalex.org/W123; landing page is the
    # work URL itself (falls back to the DOI landing page).
    external_url = item.get("id") or (f"https://doi.org/{doi}" if doi else "")

    return {
        "arxiv_id": arxiv_id,
        "title": item.get("title") or item.get("display_name") or "",
        "authors": authors,
        "abstract": abstract,
        "pdf_url": "",
        "abs_url": "",
        "published": published,
        "primary_category": primary_category,
        "source": "openalex",
        "doi": doi,
        "oa_pdf_url": oa_pdf_url,
        "external_url": external_url,
    }


@router.get("/openalex")
async def search_openalex(
    q: str = Query(..., description="OpenAlex search query"),
    max_results: int = Query(10, ge=1, le=50),
    api_key: str = Query("", description="Optional OpenAlex API key"),
    email: str = Query("", description="Optional email for OpenAlex polite pool"),
) -> Any:
    params: dict[str, Any] = {
        "search": q,
        "per_page": max_results,
    }
    if email:
        params["mailto"] = email
    if api_key:
        params["api_key"] = api_key
    url = f"{_OPENALEX_WORKS}?{urlencode(params)}"
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": "little-alphaxiv/0.1"})
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"openalex request error: {exc}") from exc

    if resp.status_code == 429:
        raise HTTPException(status_code=429, detail="openalex rate limited; try search_arxiv")
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"openalex returned status {resp.status_code}: {resp.text[:300]}",
        )
    try:
        data = resp.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=f"openalex json error: {exc}") from exc

    items = data.get("results") or []
    results = [_parse_work(it) for it in items]
    total = data.get("meta", {}).get("count", len(results)) if isinstance(data.get("meta"), dict) else len(results)
    return JSONResponse(content={"total": total, "results": results})
```

- [ ] **Step 2: Register the router (already added in Task 2's main.py edit)**

The `main.py` import + `include_router(openalex.router, ...)` were added in Task 2 Step 2. Verify both routers are present now.

- [ ] **Step 3: Confirm the app imports with both routers**

Run: `conda activate Agent_env && python -c "from backend.app.main import app; paths=sorted(r.path for r in app.routes); print([p for p in paths if 'openalex' in p or 'semantic' in p or '/search' in p])"`
Expected: `['/api/openalex', '/api/search', '/api/semantic_scholar']`, no ImportError.

- [ ] **Step 4: Smoke-test the live endpoint (optional, network-dependent)**

Run: `conda activate Agent_env && python -c "import httpx; r=httpx.get('http://127.0.0.1:8000/api/openalex?q=vision+transformer&max_results=2'); print(r.status_code, r.json()['results'][0]['title'] if r.status_code==200 else r.text[:200])"`
(Requires backend running on :8000 via `cd backend && ./run.sh` in another terminal.)
Expected: `200 <a paper title>` — confirms field mapping works against the real API. If offline, skip; the E2E driver in Task 12 intercepts this endpoint anyway.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/openalex.py
git commit -m "feat(search): OpenAlex search proxy router"
```

---

## Task 4: Extend PDF proxy with `/api/pdf-url` open-proxy route

**Files:**
- Modify: `backend/app/routers/pdf.py` (add route + hashed cache + SSRF guard)
- Test: extend `tools/verify_papershared.py`? No — `is_safe_external_url` is already tested in Task 1. The route itself is exercised by the E2E driver (Task 12).

**Interfaces:**
- Produces: `GET /api/pdf-url?url=<encoded>` — fetches an arbitrary http(s) PDF URL (SSRF-guarded via `is_safe_external_url`), caches by `sha256(url)`, streams with range support (reuses `_serve_bytes`).
- Consumes: `_papershared.is_safe_external_url` (Task 1). The existing `GET /api/pdf/{arxiv_id}` route is unchanged.

- [ ] **Step 1: Write the implementation**

Edit `backend/app/routers/pdf.py`. Add imports at the top (after the existing `import os` / `from pathlib import Path` block, merge into the existing import region):

```python
import hashlib
```

Add `from ._papershared import is_safe_external_url` to the imports (after `from fastapi.responses import ...`).

Then add a cache-path-by-url helper and the new route. Place this after the existing `_fetch_from_arxiv` function (after line 52) and before the existing `@router.get("/pdf/{arxiv_id}")` (line 55):

```python
def _cache_path_for_url(url: str) -> Path:
    # URL characters aren't filename-safe and the plain-sanitize scheme collides
    # for non-arxiv ids; key the cache by a sha256 of the URL instead.
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return _CACHE_DIR / f"oa-{digest}.pdf"


async def _fetch_from_url(url: str) -> bytes:
    ok, reason = is_safe_external_url(url)
    if not ok:
        raise HTTPException(status_code=400, detail=f"refused pdf url: {reason}")
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": "little-alphaxiv/0.1"})
        except httpx.RequestError as exc:
            raise HTTPException(status_code=502, detail=f"pdf url error: {exc}") from exc
    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"pdf url returned {resp.status_code} for {url}",
        )
    return resp.content


@router.get("/pdf-url")
async def get_pdf_by_url(
    url: str = Query(..., description="Absolute http(s) URL of an open-access PDF"),
    range_header: str | None = Header(default=None, alias="Range"),
) -> Any:
    path = _cache_path_for_url(url)
    if not path.exists():
        data = await _fetch_from_url(url)
        try:
            path.write_bytes(data)
        except OSError:
            return _serve_bytes(data, range_header)
    else:
        data = path.read_bytes()
    return _serve_bytes(data, range_header)
```

Also add `Query` to the fastapi import on line 17:
```python
from fastapi import APIRouter, Header, HTTPException, Query, Request
```
(`Request` is already imported; just add `Query`.)

- [ ] **Step 2: Confirm the app imports and the new route exists**

Run: `conda activate Agent_env && python -c "from backend.app.main import app; print([r.path for r in app.routes if 'pdf' in r.path])"`
Expected: `['/api/pdf/{arxiv_id}', '/api/pdf-url']`, no ImportError.

- [ ] **Step 3: Confirm SSRF guard rejects a private URL via the route (offline)**

Run: `conda activate Agent_env && python -c "import httpx; r=httpx.get('http://127.0.0.1:8000/api/pdf-url', params={'url':'http://127.0.0.1/x.pdf'}); print(r.status_code, r.text[:80])"`
(Backend must be running on :8000.)
Expected: `400 ... refused pdf url: ... 127.0.0.1 ...` — confirms the guard fires before any network fetch.

- [ ] **Step 4: Commit**

```bash
git add backend/app/routers/pdf.py
git commit -m "feat(pdf): open-proxy /api/pdf-url route for OA PDFs (SSRF-guarded, hashed cache)"
```

---

## Task 5: Frontend — extend `Paper` type + `searchSources` settings slice

**Files:**
- Modify: `frontend/src/types.ts:60-69` (extend `Paper`)
- Modify: `frontend/src/store/settings.ts` (add `searchSources` + setters)
- Test: `frontend/src/lib/paperSource.test.ts` (created in Task 7; the store change is covered by typecheck + Task 7's `buildSearchTools` test reading the defaults)

**Interfaces:**
- Produces (types.ts): `Paper` gains optional `source?: "arxiv" | "openalex" | "s2"`, `doi?: string`, `oa_pdf_url?: string`, `external_url?: string`.
- Produces (settings.ts): `SearchSources` interface + `searchSources: SearchSources` state + `setSearchSources(patch: Partial<SearchSources>)` action + `enabledSearchSources()` selector returning `{ openalex: boolean; s2: boolean }`. Defaults: both `enabled:false`, keys `""`.

- [ ] **Step 1: Extend the `Paper` type**

In `frontend/src/types.ts`, replace the `Paper` interface (lines 60-69):

```ts
export interface Paper {
  arxiv_id: string;
  title: string;
  authors: string[];
  abstract: string;
  pdf_url: string;
  abs_url: string;
  published: string;
  primary_category: string;
  /** Which search source surfaced this paper. arXiv results omit it (legacy). */
  source?: "arxiv" | "openalex" | "s2";
  /** DOI (lowercased, no URL wrapper) when the source provides one. */
  doi?: string;
  /** Direct open-access PDF URL for non-arXiv papers, when available. */
  oa_pdf_url?: string;
  /** Landing page (DOI/S2/OpenAlex) for papers with no in-app-previewable PDF. */
  external_url?: string;
}
```

- [ ] **Step 2: Add the `SearchSources` shape to the settings store**

In `frontend/src/store/settings.ts`, add the interface + state. First add the type after the `Theme` type definition (after line 12):

```ts
/** Optional academic search sources beyond the always-on arXiv. Keys live in
 *  the browser (localStorage) alongside provider keys; both sources also work
 *  without a key (just rate-limited), so the key is an optional enhancement. */
export interface SearchSources {
  openalex: { enabled: boolean; apiKey: string; email: string };
  semanticScholar: { enabled: boolean; apiKey: string };
}

export const DEFAULT_SEARCH_SOURCES: SearchSources = {
  openalex: { enabled: false, apiKey: "", email: "" },
  semanticScholar: { enabled: false, apiKey: "" },
};
```

Then extend `SettingsState` (add these members inside the interface, after `getCachedModels` / `clearCachedModels`):

```ts
  searchSources: SearchSources;
  /** Patch the search-sources slice (shallow-merged per source). */
  setSearchSources: (patch: Partial<SearchSources>) => void;
  /** Resolve which optional sources are currently enabled (for tool building). */
  enabledSearchSources: () => { openalex: boolean; s2: boolean };
```

- [ ] **Step 3: Implement the state + actions in the store body**

In the `create<SettingsState>()(persist((set, get) => ({ ... }))` body, after `theme: DEFAULT_THEME,` (line 45) add:

```ts
      searchSources: DEFAULT_SEARCH_SOURCES,
```

And after the `clearCachedModels` action (after line 106), add:

```ts
      setSearchSources: (patch) =>
        set((s) => ({
          searchSources: {
            openalex: { ...s.searchSources.openalex, ...(patch.openalex ?? {}) },
            semanticScholar: {
              ...s.searchSources.semanticScholar,
              ...(patch.semanticScholar ?? {}),
            },
          },
        })),
      enabledSearchSources: () => {
        const s = get().searchSources;
        return { openalex: s.openalex.enabled, s2: s.semanticScholar.enabled };
      },
```

- [ ] **Step 4: Merge persisted defaults on rehydrate**

In the `persist` options (the `onRehydrateStorage` block, lines 113-115), coerce missing `searchSources` so older localStorage without it still works. Replace the `onRehydrateStorage` callback:

```ts
      onRehydrateStorage: () => (state) => {
        if (state) {
          state.theme = coerceTheme(state.theme);
          // Older persisted state (pre multi-source) has no searchSources.
          if (!state.searchSources) state.searchSources = DEFAULT_SEARCH_SOURCES;
        }
      },
```

- [ ] **Step 5: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/store/settings.ts
git commit -m "feat(settings): searchSources slice + Paper source/doi/oa fields"
```

---

## Task 6: Frontend — API client functions

**Files:**
- Modify: `frontend/src/lib/api.ts` (add `searchOpenAlex`, `searchSemanticScholar`, `pdfUrlForOa`)

**Interfaces:**
- Produces:
  - `searchOpenAlex(query: string, max: number, opts: { apiKey?: string; email?: string }): Promise<{ total: number; results: Paper[] }>`
  - `searchSemanticScholar(query: string, max: number, apiKey?: string): Promise<{ total: number; results: Paper[] }>`
  - `pdfUrlForOa(url: string): string` — returns `/api/pdf-url?url=<encoded>`.
- Consumes: `Paper` type (Task 5).

- [ ] **Step 1: Add the functions**

In `frontend/src/lib/api.ts`, add after the existing `searchArxiv` function (after line 225) and before `webSearch`:

```ts
/** OpenAlex search via the backend proxy. */
export async function searchOpenAlex(
  query: string,
  maxResults = 8,
  opts: { apiKey?: string; email?: string } = {}
): Promise<{ total: number; results: Paper[] }> {
  const params = new URLSearchParams({
    q: query,
    max_results: String(maxResults),
  });
  if (opts.apiKey) params.set("api_key", opts.apiKey);
  if (opts.email) params.set("email", opts.email);
  const r = await fetch(`${BASE}/api/openalex?${params.toString()}`);
  if (!r.ok) throw new Error(`openalex search error ${r.status}`);
  return r.json();
}

/** Semantic Scholar search via the backend proxy. */
export async function searchSemanticScholar(
  query: string,
  maxResults = 8,
  apiKey?: string
): Promise<{ total: number; results: Paper[] }> {
  const params = new URLSearchParams({
    q: query,
    max_results: String(maxResults),
  });
  if (apiKey) params.set("api_key", apiKey);
  const r = await fetch(`${BASE}/api/semantic_scholar?${params.toString()}`);
  if (!r.ok) throw new Error(`semantic scholar search error ${r.status}`);
  return r.json();
}
```

Then add `pdfUrlForOa` right after the existing `pdfUrl` function (after line 242):

```ts
/** URL for an arbitrary open-access PDF, served through the backend open proxy
 *  (CORS + cache + SSRF guard). Used by non-arXiv results that carry oa_pdf_url. */
export function pdfUrlForOa(url: string): string {
  return `${BASE}/api/pdf-url?url=${encodeURIComponent(url)}`;
}
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/lib/api.ts
git commit -m "feat(api): searchOpenAlex / searchSemanticScholar / pdfUrlForOa"
```

---

## Task 7: Frontend — pure `paperSource` helpers + Vitest

**Files:**
- Create: `frontend/src/lib/paperSource.ts`
- Create: `frontend/src/lib/paperSource.test.ts`
- Modify: `frontend/src/lib/llm.ts` (add `buildSearchTools`) — the dispatch branches land in Task 8.

**Interfaces:**
- Produces (paperSource.ts):
  - `resolvePaperId(p: Paper): string` — stable opaque id: bare arXiv id if present, else `doi:<doi>` if DOI, else `<source>:<sourceId-ish>`. (For the no-id/no-OA case the card never opens in-app, so the exact fallback string only needs to be a stable React key.)
  - `openTarget(p: Paper): { kind: "arxiv"; id: string } | { kind: "oa"; id: string; url: string } | { kind: "external"; url: string }` — the 3-way click routing decision.
  - `buildSearchTools(sources: { openalex: boolean; s2: boolean }): ToolDef[]` — returns `[search_arxiv, search_openalex?, search_semantic_scholar?, web_search]`.
- Consumes: `Paper`, `ToolDef` types (Task 5 / existing).

- [ ] **Step 1: Write the failing test**

Create `frontend/src/lib/paperSource.test.ts`:

```ts
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
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/lib/paperSource.test.ts`
Expected: FAIL — `resolvePaperId is not defined` / module not found.

- [ ] **Step 3: Write the implementation**

Create `frontend/src/lib/paperSource.ts`:

```ts
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/lib/paperSource.test.ts`
Expected: all tests PASS.

- [ ] **Step 5: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/paperSource.ts frontend/src/lib/paperSource.test.ts
git commit -m "feat(search): pure paper-id resolution + tool-list builder + tests"
```

---

## Task 8: Frontend — wire `buildSearchTools` + dispatch branches into the tool loop

**Files:**
- Modify: `frontend/src/lib/llm.ts` (replace static `SEARCH_TOOLS` usage in `runConversation`; add `search_openalex` + `search_semantic_scholar` branches; generalize title label)
- Modify: `frontend/src/components/ChatPanel.tsx` (pass enabled sources + search-source creds into `runConversation`)

**Interfaces:**
- Produces: `runConversation` gains an `enabledSources` + `searchSourceCreds` option; builds tools via `buildSearchTools`.
- Consumes: `buildSearchTools` (Task 7), `searchOpenAlex` / `searchSemanticScholar` (Task 6), settings store (Task 5).

- [ ] **Step 1: Add the option + tool-building to `runConversation`**

In `frontend/src/lib/llm.ts`, update the imports (line 9) to pull in the new api fns and the tool builder:

```ts
import { streamChat, completeChat, searchArxiv, webSearch, searchOpenAlex, searchSemanticScholar } from "./api";
```

Add to imports (line 10 area):
```ts
import { buildSearchTools } from "./paperSource";
```

Extend the `runConversation` opts type — add `enabledSources` and `searchSourceCreds`. Replace the `export async function runConversation(opts: {` block's opening (lines 71-78) so it reads:

```ts
export async function runConversation(opts: {
  provider: Provider;
  messages: ChatMessage[];
  systemPrompt?: string;
  model?: string; // per-conversation model override
  signal?: AbortSignal;
  callbacks: LoopCallbacks;
  enabledSources?: { openalex: boolean; s2: boolean };
  searchSourceCreds?: { openalex: { apiKey: string; email: string }; semanticScholar: { apiKey: string } };
}): Promise<{ newMessages: ChatMessage[] }> {
  const { provider, messages, systemPrompt, model: modelOverride, signal, callbacks, enabledSources, searchSourceCreds } = opts;
  const effectiveModel = modelOverride || provider.model;
  const tools = buildSearchTools(enabledSources ?? { openalex: false, s2: false });
```

Then replace the `tools: SEARCH_TOOLS,` line inside the `streamChat` call (line 122) with:

```ts
      tools,
```

- [ ] **Step 2: Add the two new dispatch branches**

In the tool-dispatch `for (const tc of result.tool_calls)` loop, add the two branches right after the `search_arxiv` branch (after line 178, before `} else if (tc.function.name === "web_search") {`). Insert:

```ts
      } else if (tc.function.name === "search_openalex") {
        callbacks.onStatus?.("Searching OpenAlex…");
        try {
          const res = await searchOpenAlex(
            args.query ?? "",
            args.max_results ?? 8,
            searchSourceCreds?.openalex ?? { apiKey: "", email: "" }
          );
          callbacks.onPapers?.(res.results);
          const toolMsg: ChatMessage = {
            role: "tool",
            tool_call_id: tc.id,
            name: "search_openalex",
            content: JSON.stringify(res.results.slice(0, 8)),
            ui: { papers: res.results },
          };
          apiMessages.push({
            role: "tool",
            tool_call_id: tc.id,
            name: "search_openalex",
            content: JSON.stringify(res.results.slice(0, 8)),
          });
          newMessages.push(toolMsg);
          callbacks.onToolMessage?.(toolMsg);
        } catch (e: any) {
          const msg = `openalex search failed (${e?.message ?? "error"}); try search_arxiv`;
          apiMessages.push({ role: "tool", tool_call_id: tc.id, name: "search_openalex", content: msg });
          newMessages.push({ role: "tool", tool_call_id: tc.id, name: "search_openalex", content: msg });
          callbacks.onStatus?.("");
        }
      } else if (tc.function.name === "search_semantic_scholar") {
        callbacks.onStatus?.("Searching Semantic Scholar…");
        try {
          const res = await searchSemanticScholar(
            args.query ?? "",
            args.max_results ?? 8,
            searchSourceCreds?.semanticScholar?.apiKey
          );
          callbacks.onPapers?.(res.results);
          const toolMsg: ChatMessage = {
            role: "tool",
            tool_call_id: tc.id,
            name: "search_semantic_scholar",
            content: JSON.stringify(res.results.slice(0, 8)),
            ui: { papers: res.results },
          };
          apiMessages.push({
            role: "tool",
            tool_call_id: tc.id,
            name: "search_semantic_scholar",
            content: JSON.stringify(res.results.slice(0, 8)),
          });
          newMessages.push(toolMsg);
          callbacks.onToolMessage?.(toolMsg);
        } catch (e: any) {
          const msg = `semantic scholar search failed (${e?.message ?? "error"}); try search_arxiv`;
          apiMessages.push({ role: "tool", tool_call_id: tc.id, name: "search_semantic_scholar", content: msg });
          newMessages.push({ role: "tool", tool_call_id: tc.id, name: "search_semantic_scholar", content: msg });
          callbacks.onStatus?.("");
        }
```

- [ ] **Step 3: Generalize the title-gen label (cosmetic)**

In `frontend/src/lib/llm.ts`, in `generateConversationTitle` (the `paperBlock` string, line 259), change `arxiv id:` to `paper id:`:

```ts
      `paper id: ${paperContext.arxivId ?? ""}\n` +
```

(The `arxivId` field name stays — it's the opaque key — but the visible label is now source-neutral.)

- [ ] **Step 4: Pass the enabled sources + creds from ChatPanel**

In `frontend/src/components/ChatPanel.tsx`, inside `send()` where `runConversation({...})` is called (around lines 259-305), add the two new options. After the `model: c.model,` line in the `runConversation({` call, add:

```ts
        enabledSources: enabledSources,
        searchSourceCreds: {
          openalex: searchSources.openalex,
          semanticScholar: searchSources.semanticScholar,
        },
```

Then wire the store values at the top of the component. After the `provider` selector (line 98), add:

```ts
  const enabledSources = useSettings((s) => s.enabledSearchSources());
  const searchSources = useSettings((s) => s.searchSources);
```

- [ ] **Step 5: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/llm.ts frontend/src/components/ChatPanel.tsx
git commit -m "feat(search): wire buildSearchTools + openalex/s2 dispatch + fallback"
```

---

## Task 9: Frontend — PaperCard source badge + dynamic CTA

**Files:**
- Modify: `frontend/src/components/PaperCard.tsx`

**Interfaces:**
- Produces: `PaperCard` shows a source badge and a CTA that reflects openability.
- Consumes: `Paper`, `openTarget` (Task 7).

- [ ] **Step 1: Update the component**

Replace the whole `frontend/src/components/PaperCard.tsx` content:

```tsx
// Clickable paper card rendered from a search tool result. arXiv and
// open-access results open the in-app preview; non-previewable results open
// their external landing page.

import type { Paper } from "../types";
import { openTarget } from "../lib/paperSource";

const SOURCE_BADGE: Record<string, string> = {
  arxiv: "arXiv",
  openalex: "OpenAlex",
  s2: "S2",
};

export function PaperCard({ paper, onClick }: { paper: Paper; onClick: () => void }) {
  const target = openTarget(paper);
  const previewable = target.kind === "arxiv" || target.kind === "oa";
  const badge = paper.source ? SOURCE_BADGE[paper.source] ?? paper.source : "arXiv";
  return (
    <button className="paper-card" onClick={onClick}>
      <div className="paper-card-title">{paper.title}</div>
      <div className="paper-card-authors">{paper.authors.slice(0, 4).join(", ")}{paper.authors.length > 4 ? " et al." : ""}</div>
      <div className="paper-card-meta">
        <span className="paper-id">{paper.arxiv_id || paper.doi || ""}</span>
        <span className="paper-cat">{badge}</span>
        {paper.primary_category && <span className="paper-cat">{paper.primary_category}</span>}
        {paper.published && (
          <span className="paper-date">{paper.published.slice(0, 7)}</span>
        )}
      </div>
      <div className="paper-card-abstract">{paper.abstract.slice(0, 240)}{paper.abstract.length > 240 ? "…" : ""}</div>
      <div className="paper-card-cta">{previewable ? "Click to preview PDF →" : "Open externally →"}</div>
    </button>
  );
}
```

- [ ] **Step 2: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/PaperCard.tsx
git commit -m "feat(search): paper card source badge + openable-aware CTA"
```

---

## Task 10: Frontend — ChatPanel 3-way open routing + metadata seeding

**Files:**
- Modify: `frontend/src/components/ChatPanel.tsx` (`onOpenPaper` + `MessageRow`)
- Modify: `frontend/src/components/PdfViewer.tsx` (accept `pdfUrlOverride`, seed metadata)

**Interfaces:**
- Produces: `onOpenPaper(paper: Paper)` — seeds the paper into IDB then navigates. `MessageRow` calls `onOpenPaper(p)`. `PdfViewer` accepts an optional `pdfUrlOverride` and, when set, uses `pdfUrlForOa` instead of `pdfUrl(arxivId)`.
- Consumes: `openTarget` (Task 7), `db.savePaper`, `pdfUrlForOa` (Task 6).

- [ ] **Step 1: Change `onOpenPaper` to take a `Paper` and seed metadata**

In `frontend/src/components/ChatPanel.tsx`, replace the `onOpenPaper` callback (line 110) with one that seeds metadata for all sources (fixes the empty-metadata gap for arXiv too) and routes 3 ways:

```ts
  // Seed the paper's metadata into IDB (so PaperView/PdfViewer show real
  // title/authors/abstract instead of the bare-id fallback), then open it.
  // arXiv-id papers -> existing /api/pdf path; OA papers -> /api/pdf-url
  // proxy; otherwise -> external landing page in a new tab.
  const onOpenPaper = useCallback(async (paper: Paper) => {
    const target = openTarget(paper);
    if (target.kind === "external") {
      if (target.url) window.open(target.url, "_blank", "noopener,noreferrer");
      return;
    }
    await db.savePaper({
      arxiv_id: target.id,
      title: paper.title,
      authors: paper.authors,
      abstract: paper.abstract,
      pdf_url: paper.pdf_url,
      abs_url: paper.abs_url,
      published: paper.published,
      primary_category: paper.primary_category,
      ...(paper.source ? { source: paper.source } : {}),
      ...(paper.doi ? { doi: paper.doi } : {}),
      ...(target.kind === "oa" ? { oa_pdf_url: target.url } : {}),
      fetched_at: Date.now(),
    });
    navigate(`/paper/${encodeURIComponent(target.id)}`);
  }, [navigate]);
```

Add the `openTarget` import at the top of `ChatPanel.tsx` (with the other lib imports near line 19):

```ts
import { openTarget } from "../lib/paperSource";
```

(`db` is already imported as `* as db` on line 18.)

- [ ] **Step 2: Update `MessageRow` to pass the paper**

The `MessageRow` signature (line 490) and its tool branch (line 512) currently pass `p.arxiv_id`. Change the prop type and the call. Replace the `MessageRow` signature + tool branch:

```tsx
const MessageRow = memo(function MessageRow({
  msg,
  showPaperLinks,
  onOpenPaper,
}: {
  msg: ChatMessage;
  showPaperLinks: boolean;
  onOpenPaper: (paper: Paper) => void;
}) {
```

And the tool branch (the `papers.map` line):

```tsx
        {showPaperLinks &&
          papers.map((p) => <PaperCard key={p.arxiv_id || p.doi || `p${papers.indexOf(p)}`} paper={p} onClick={() => onOpenPaper(p)} />)}
```

- [ ] **Step 3: Make `PdfViewer` honor an OA override URL**

In `frontend/src/components/PdfViewer.tsx`, change the `Props` interface (lines 24-28) to accept an optional override:

```ts
interface Props {
  arxivId: string;
  /** When set (non-arXiv OA papers), load the PDF from /api/pdf-url?url=…
   *  instead of the arxiv-id path. */
  pdfUrlOverride?: string;
  onLoaded?: (numPages: number) => void;
  onTextExtracted?: (text: string) => void;
}
```

Update the function signature (line 30) and the `getDocument` call (line 47):

```ts
export function PdfViewer({ arxivId, pdfUrlOverride, onLoaded, onTextExtracted }: Props) {
```

```ts
    pdfjsLib
      .getDocument({ url: pdfUrlOverride || pdfUrl(arxivId) })
```

Add `pdfUrlForOa` is NOT needed here — the override is the full proxy URL string, built by PaperView. But add the import of `pdfUrlForOa` to `api.ts` consumers only where used (PaperView, Task 11). In PdfViewer, only `pdfUrl` is imported (line 13) — leave it; the override string is passed in directly.

- [ ] **Step 4: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors. (PaperView still passes `arxivId` only — updated in Task 11.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/ChatPanel.tsx frontend/src/components/PdfViewer.tsx
git commit -m "feat(search): 3-way paper open routing + metadata seeding + OA pdf override"
```

---

## Task 11: Frontend — PaperView passes OA override + generalized preamble; ChatView dynamic prompt

**Files:**
- Modify: `frontend/src/views/PaperView.tsx` (generalize `PAPER_SYSTEM_PREAMBLE`; read `oa_pdf_url` from IDB and pass to PdfViewer)
- Modify: `frontend/src/views/ChatView.tsx` (dynamic `GENERAL_SYSTEM_PROMPT` from enabled sources)

**Interfaces:**
- Produces: `PaperView` builds `pdfUrlForOa` when the cached paper has `oa_pdf_url`; `PAPER_SYSTEM_PREAMBLE` is source-neutral. `ChatView` exports `buildGeneralSystemPrompt(sources)`.
- Consumes: `pdfUrlForOa` (Task 6), `enabledSearchSources` (Task 5).

- [ ] **Step 1: Generalize the preamble in PaperView**

In `frontend/src/views/PaperView.tsx`, replace `PAPER_SYSTEM_PREAMBLE` (lines 256-262):

```ts
function PAPER_SYSTEM_PREAMBLE(paperId: string): string {
  return `You are a research assistant helping the user read and understand the paper ${paperId}.
The user can see the PDF on the left and is chatting with you on the right.
The full text of the paper is provided below. Answer questions about its content - methods, datasets, results, limitations, related work - grounded in the provided text.
If the user asks something not covered in the paper, say so. Be concise and precise. Format answers with markdown.
You have paper-search tools (search_arxiv, and optionally search_openalex / search_semantic_scholar) if the user wants to find related papers.`;
}
```

The two call sites `PAPER_SYSTEM_PREAMBLE(arxivId!)` (lines 113, 114) keep working — `arxivId` is the opaque key.

- [ ] **Step 2: Read `oa_pdf_url` from IDB and pass it to PdfViewer**

In `PaperView.tsx`, add state for the OA override and populate it from the cached paper. Add the import of `pdfUrlForOa`:

```ts
import { pdfUrlForOa } from "../lib/api";
```

Add a state var near the other `useState` calls (after line 37):

```ts
  const [pdfUrlOverride, setPdfUrlOverride] = useState<string | undefined>(undefined);
```

In the existing `db.getPaper(arxivId)` effect (lines 44-49), also capture the OA url. Replace that effect:

```ts
  useEffect(() => {
    if (!arxivId) return;
    db.getPaper(arxivId).then((p) => {
      if (p?.full_text) { setFullText(p.full_text); setExtracting(false); }
      if (p?.oa_pdf_url) setPdfUrlOverride(pdfUrlForOa(p.oa_pdf_url));
    });
  }, [arxivId]);
```

Pass the override to PdfViewer (line 144):

```tsx
        left={<PdfViewer arxivId={arxivId} pdfUrlOverride={pdfUrlOverride} onTextExtracted={onTextExtracted} />}
```

- [ ] **Step 3: Make ChatView's system prompt dynamic**

In `frontend/src/views/ChatView.tsx`, replace the `GENERAL_SYSTEM_PROMPT` export (lines 36-42) with a builder + a default, and use the builder in the view. Replace lines 36-42:

```ts
/** Build the general-chat system prompt from the user's enabled search sources.
 *  arXiv is always available; OpenAlex / Semantic Scholar tools appear only
 *  when enabled, so the prompt tells the model which sources it has. */
export function buildGeneralSystemPrompt(sources: { openalex: boolean; s2: boolean }): string {
  const extras: string[] = [];
  if (sources.openalex) extras.push("search_openalex (broad published literature across all fields)");
  if (sources.s2) extras.push("search_semantic_scholar (Semantic Scholar's 214M-paper graph)");
  const sourceLine = extras.length
    ? `You also have ${extras.join(" and ")} for broader or published-literature searches; prefer the most relevant source per query.`
    : "";
  return `You are a helpful research assistant integrated into a paper-reading app.
Help the user find academic papers using the search_arxiv tool (always available).
When the user asks for papers on a topic, call the most fitting search tool with concise keywords.
After results return, summarize the most relevant ones in 1-2 sentences each and let the user click to preview.
${sourceLine}
You can also use web_search for non-academic questions.
Be concise. Prefer calling a paper-search tool over answering from memory when the user wants papers.
Any arxiv.org links you write render as in-app preview cards the user can click to read the paper — so citing a paper by its arXiv link is fine and never opens an external site.`;
}
```

Then make the view use it. Replace the `ChatView` body's `ChatPanel` line (line 30) — it needs the enabled sources. Add the settings selector to the component and compute the prompt. Replace the whole `ChatView` function (lines 13-34):

```tsx
export function ChatView() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const conversations = useConversations((s) => s.conversations);
  const loaded = useConversations((s) => s.loaded);
  const enabledSources = useSettings((s) => s.enabledSearchSources());
  const systemPrompt = buildGeneralSystemPrompt(enabledSources);

  useEffect(() => {
    if (!loaded || !id) return;
    if (!conversations.some((c) => c.id === id)) {
      navigate("/", { replace: true });
    }
  }, [loaded, id, conversations, navigate]);

  if (!id) return <Navigate to="/" replace />;
  return (
    <main className="main-pane">
      <div className="chat-shell">
        <ChatPanel conversationId={id} systemPrompt={systemPrompt} />
      </div>
    </main>
  );
}
```

Add the missing import at the top of `ChatView.tsx`:

```ts
import { useSettings } from "../store/settings";
```

- [ ] **Step 4: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/views/PaperView.tsx frontend/src/views/ChatView.tsx
git commit -m "feat(search): dynamic general prompt + OA pdf override + source-neutral preamble"
```

---

## Task 12: Frontend — Settings UI "Search sources" section

**Files:**
- Modify: `frontend/src/views/SettingsView.tsx`

**Interfaces:**
- Produces: a new `<h2>Search sources</h2>` section between Appearance and Providers, with arXiv always-on row, OpenAlex toggle+key+email+link, Semantic Scholar toggle+key+link.

- [ ] **Step 1: Add the section to SettingsView**

In `frontend/src/views/SettingsView.tsx`, add the store selectors inside the component (after line 26, the `getCachedModels` selector):

```ts
  const searchSources = useSettings((s) => s.searchSources);
  const setSearchSources = useSettings((s) => s.setSearchSources);
```

Then insert the new section markup between the Appearance `</div>` (end of `.theme-grid`, line 88) and `<h2>Providers</h2>` (line 90). Insert:

```tsx
        <h2>Search sources</h2>
        <p className="settings-hint">
          arXiv is always on. Optionally enable OpenAlex and Semantic Scholar
          for broader (published, peer-reviewed) literature. Both work without a
          key (just rate-limited); an API key raises your limits. Keys are stored
          only in your browser.
        </p>
        <div className="search-sources-list">
          <div className="search-source-item">
            <div className="search-source-row">
              <strong>arXiv</strong>
              <span className="badge">always on</span>
            </div>
            <div className="provider-detail">Preprints — the default source.</div>
          </div>

          <div className={`search-source-item ${searchSources.openalex.enabled ? "enabled" : ""}`}>
            <div className="search-source-row">
              <strong>OpenAlex</strong>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={searchSources.openalex.enabled}
                  onChange={(e) => setSearchSources({ openalex: { ...searchSources.openalex, enabled: e.target.checked } })}
                />
                <span>{searchSources.openalex.enabled ? "on" : "off"}</span>
              </label>
            </div>
            <div className="provider-detail">
              API key (optional):{" "}
              <input
                className="search-source-key"
                type="password"
                placeholder="optional"
                value={searchSources.openalex.apiKey}
                onChange={(e) => setSearchSources({ openalex: { ...searchSources.openalex, apiKey: e.target.value } })}
              />
              {" · "}email (polite pool, optional):{" "}
              <input
                className="search-source-key"
                type="text"
                placeholder="optional"
                value={searchSources.openalex.email}
                onChange={(e) => setSearchSources({ openalex: { ...searchSources.openalex, email: e.target.value } })}
              />
            </div>
            <div className="provider-detail">
              Get a free key at{" "}
              <a href="https://openalex.org/settings/api" target="_blank" rel="noopener noreferrer">openalex.org/settings/api</a>
              {" "}($1/day free usage without one).
            </div>
          </div>

          <div className={`search-source-item ${searchSources.semanticScholar.enabled ? "enabled" : ""}`}>
            <div className="search-source-row">
              <strong>Semantic Scholar</strong>
              <label className="toggle">
                <input
                  type="checkbox"
                  checked={searchSources.semanticScholar.enabled}
                  onChange={(e) => setSearchSources({ semanticScholar: { ...searchSources.semanticScholar, enabled: e.target.checked } })}
                />
                <span>{searchSources.semanticScholar.enabled ? "on" : "off"}</span>
              </label>
            </div>
            <div className="provider-detail">
              API key (optional):{" "}
              <input
                className="search-source-key"
                type="password"
                placeholder="optional"
                value={searchSources.semanticScholar.apiKey}
                onChange={(e) => setSearchSources({ semanticScholar: { ...searchSources.semanticScholar, apiKey: e.target.value } })}
              />
            </div>
            <div className="provider-detail">
              Request a free key at{" "}
              <a href="https://www.semanticscholar.org/product/api#api-key" target="_blank" rel="noopener noreferrer">semanticscholar.org/product/api</a>
              {" "}(1 req/sec with a key; shared pool without).
            </div>
          </div>
        </div>
```

- [ ] **Step 2: Add minimal CSS for the new section**

Find the frontend stylesheet (grep `paper-card` to locate — it's `frontend/src/index.css`). Add at the end of `index.css`:

```css
/* Settings: search sources */
.search-sources-list { display: flex; flex-direction: column; gap: 10px; }
.search-source-item { padding: 10px 12px; border: 1px solid var(--border); border-radius: 8px; }
.search-source-item.enabled { border-color: var(--accent, #4a90d9); }
.search-source-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.search-source-item .toggle { display: inline-flex; align-items: center; gap: 6px; cursor: pointer; font-size: 0.85em; opacity: 0.8; }
.search-source-key { font-size: 0.9em; padding: 2px 6px; }
```

- [ ] **Step 3: Run typecheck**

Run: `cd frontend && npm run typecheck`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/views/SettingsView.tsx frontend/src/index.css
git commit -m "feat(settings): Search sources section (OpenAlex + Semantic Scholar toggles + keys + links)"
```

---

## Task 13: E2E — extend mock_llm + add multi-source driver

**Files:**
- Modify: `tools/mock_llm.py` (recognize `search_openalex` / `search_semantic_scholar` tool calls; emit canned papers including one OA + one external-only)
- Create: `tools/drive_multisource.py`

**Interfaces:**
- Produces: mock emits, on turn 1, whichever paper-search tool the model calls — but the mock is deterministic, so we make it emit `search_openalex` when the user's query mentions "openalex"/"broad" (so the driver can force the path). The driver intercepts `/api/openalex`, `/api/semantic_scholar`, `/api/pdf-url` with `page.route`, asserting badge + OA-open-in-iframe/PdfViewer + external-open.

- [ ] **Step 1: Extend mock_llm to emit a `search_openalex` tool call**

In `tools/mock_llm.py`, the turn-1 branch (lines 138-218) currently always emits `search_arxiv`. Add a variant: if any user message contains "openalex" (case-insensitive), emit `search_openalex` instead with the same streaming structure. Insert this check right after `if not _has_tool_result(messages):` (line 138), before the existing `search_arxiv` emit. The cleanest change: compute the tool name + args once, then emit. Replace the `if not _has_tool_result(messages):` block's tool-name/args setup. Concretely, add a helper near `_is_title_request`:

```python
def _requested_source(messages: list) -> str:
    """If the user's query mentions a specific source, the mock emits that
    source's tool call so the E2E driver can exercise each path deterministically
    (the real model picks; the mock is fixed). Returns 'search_arxiv' by default."""
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            low = c.lower()
            if "openalex" in low:
                return "search_openalex"
            if "semantic" in low or " s2" in low:
                return "search_semantic_scholar"
    return "search_arxiv"
```

Then in the turn-1 branch, replace the hardcoded `"search_arxiv"` name + the `{"query": "vision transformer", "max_results": 5}` args with `tool_name = _requested_source(messages)` and `args = json.dumps({"query": "vision transformer", "max_results": 5})`, using `tool_name` in both the non-stream and stream paths (the `function.name` and the streamed header `function: {"name": tool_name, ...}`). The `tool_call_id` stays `"call_mock_1"`. The turn-2 markdown answer is unchanged.

- [ ] **Step 2: Write the driver**

Create `tools/drive_multisource.py`:

```python
"""Verify multi-source search (OpenAlex + Semantic Scholar) end-to-end with no
real key/network. Uses mock_llm.py (forced to emit search_openalex) and
intercepts the new backend endpoints with page.route.

Checks:
  1. Enabling OpenAlex in Settings makes search_openalex appear in the tool list
     (the mock calls it on turn 1 because the query says "openalex").
  2. A paper card renders with the OpenAlex source badge.
  3. An OA paper card (oa_pdf_url set) opens the in-app PDF preview
     (/api/pdf-url is requested, PdfViewer mounts).
  4. An external-only card (no oa_pdf_url) opens the external_url in a new tab
     (window.open intercepted).
  5. No page errors.

Run (three servers up: backend :8000, frontend :5173, mock_llm :5050):
    conda activate Agent_env
    python tools/drive_multisource.py
"""
import codecs, json, os, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

sys.stdout = codecs.getwriter("utf-8")(sys.stdout.buffer, errors="replace")
APP = os.environ.get("APP_URL", "http://127.0.0.1:5173")
PROV = {
    "name": "mock",
    "base_url": "http://127.0.0.1:5050/v1",
    "api_key": "mock",
    "model": "mock-model",
}
SHOTS = Path(__file__).parent / "shots" / "multisource"
SHOTS.mkdir(parents=True, exist_ok=True)

# Canned OpenAlex results: one OA paper (opens in-app), one external-only.
OA_RESULTS = {
    "total": 2,
    "results": [
        {
            "arxiv_id": "", "doi": "10.1000/oa-paper",
            "title": "An Open Access Paper On Vision Transformers",
            "authors": ["Ada Lovelace", "Alan Turing"],
            "abstract": "We study vision transformers with open access.",
            "pdf_url": "", "abs_url": "", "published": "2024-03-01",
            "primary_category": "Computer Vision",
            "source": "openalex",
            "oa_pdf_url": "https://example.org/oa-paper.pdf",
            "external_url": "https://doi.org/10.1000/oa-paper",
        },
        {
            "arxiv_id": "", "doi": "10.1000/paywall-paper",
            "title": "A Paywalled Paper Behind A Publisher Login",
            "authors": ["Grace Hopper"],
            "abstract": "This one has no OA PDF.",
            "pdf_url": "", "abs_url": "", "published": "2023-11-01",
            "primary_category": "",
            "source": "openalex",
            "oa_pdf_url": "",
            "external_url": "https://doi.org/10.1000/paywall-paper",
        },
    ],
}
# Minimal valid 1-page PDF bytes ("%PDF-1.4\n...%%EOF") for the pdf-url proxy.
MIN_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"


def seed(page):
    page.goto(f"{APP}/settings", wait_until="networkidle")
    page.evaluate(
        """(pj)=>{
          const p=JSON.parse(pj);
          localStorage.setItem('little-alphaxiv-settings',
            JSON.stringify({state:{
              providers:[Object.assign({id:'r'},p,{is_default:true})],
              defaultProviderId:'r',theme:'dark',
              searchSources:{openalex:{enabled:true,apiKey:'',email:''},
                             semanticScholar:{enabled:false,apiKey:''}}
            },version:0}));
        }""",
        json.dumps(PROV),
    )
    page.evaluate("""async ()=>{
      const req=indexedDB.deleteDatabase('little-alphaxiv');
      await new Promise(r=>{req.onsuccess=r;req.onerror=r;req.onblocked=r;});
    }""")


with sync_playwright() as pw:
    b = pw.chromium.launch(headless=True)
    page = b.new_context(viewport={"width": 1500, "height": 950}).new_page()
    logs = []
    page.on("pageerror", lambda e: logs.append(str(e)))

    # Intercept the new endpoints so no real network is needed.
    opened_external = {"url": None}
    page.route("**/api/openalex**", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps(OA_RESULTS)))
    page.route("**/api/pdf-url**", lambda r: r.fulfill(status=200, content_type="application/pdf", body=MIN_PDF))
    page.expose_binding("__lax_open_external", lambda src, url: opened_external.update(url=url))

    # Capture window.open for the external-only card.
    page.add_init_script("window.open = (u)=>{ window.__lax_open_external(u); return null; };")

    seed(page)
    page.goto(APP, wait_until="networkidle")
    page.wait_for_timeout(800)

    # Query mentions "openalex" -> mock emits search_openalex.
    page.locator("textarea").first.fill("find me openalex papers on vision transformers")
    page.locator("button:has-text('Send')").click()

    # Wait for the paper cards.
    page.wait_for_selector(".paper-card", timeout=20000)
    badges = page.evaluate("()=>[...document.querySelectorAll('.paper-card .paper-cat')].map(e=>e.textContent.trim())")
    cta_texts = page.evaluate("()=>[...document.querySelectorAll('.paper-card-cta')].map(e=>e.textContent.trim())")
    has_openalex_badge = any("OpenAlex" in b for b in badges)
    print("BADGES:", badges)
    print("CTAS:", cta_texts)
    print("HAS_OPENALEX_BADGE:", has_openalex_badge)
    page.screenshot(path=str(SHOTS / "cards.png"))

    # Click the OA card (first one) -> should open /api/pdf-url + PdfViewer.
    page.locator(".paper-card").first.click()
    page.wait_for_timeout(1500)
    pdf_visible = page.evaluate("()=>!!document.querySelector('.pdf-viewer')")
    print("OA_PDF_VIEWER_MOUNTED:", pdf_visible)
    page.screenshot(path=str(SHOTS / "oa_preview.png"))

    # Go back to chat and click the external-only card (second).
    page.go_back()
    page.wait_for_selector(".paper-card", timeout=10000)
    page.locator(".paper-card").nth(1).click()
    page.wait_for_timeout(800)
    print("EXTERNAL_OPENED_URL:", opened_external["url"])
    external_ok = opened_external["url"] == "https://doi.org/10.1000/paywall-paper"

    print("PAGEERRORS:", logs)
    b.close()

    ok = has_openalex_badge and pdf_visible and external_ok and not logs
    print("VERDICT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
```

- [ ] **Step 3: Run the full E2E rig**

Start the three servers (each in its own terminal / background):
1. `cd backend && ./run.sh`
2. `cd frontend && npm run dev`
3. `conda activate Agent_env && python tools/mock_llm.py`

Then run:
`conda activate Agent_env && python tools/drive_multisource.py`
Expected: `HAS_OPENALEX_BADGE: True`, `OA_PDF_VIEWER_MOUNTED: True`, `EXTERNAL_OPENED_URL: https://doi.org/10.1000/paywall-paper`, `PAGEERRORS: []`, `VERDICT: PASS`, exit 0.

- [ ] **Step 4: Commit**

```bash
git add tools/mock_llm.py tools/drive_multisource.py
git commit -m "test(e2e): multi-source driver + mock openalex/s2 tool calls"
```

---

## Task 14: Full verification + merge

**Files:** none (verification + merge only)

- [ ] **Step 1: Run the full frontend test suite + typecheck**

Run:
```bash
cd frontend
npm run typecheck
npm test
```
Expected: typecheck clean; all Vitest tests pass (existing 4 files + new `paperSource.test.ts`).

- [ ] **Step 2: Run the existing E2E regression drivers to confirm no breakage**

Run (with the three servers up from Task 13):
```bash
conda activate Agent_env && python tools/drive_titles.py
conda activate Agent_env && python tools/drive_multisource.py
conda activate Agent_env && python tools/verify_papershared.py
```
Expected: `drive_titles.py` `VERDICT: PASS` (arXiv path + titles + date groups still work — `search_arxiv` unchanged); `drive_multisource.py` `VERDICT: PASS`; `verify_papershared.py` `VERDICT: PASS`.

- [ ] **Step 3: Merge the worktree branch back to main**

```bash
git checkout main
git merge --no-ff <worktree-branch-name> -m "Merge multi-source-search: OpenAlex + Semantic Scholar + OA PDF in-app preview"
```

If conflicts, resolve them. (Per CLAUDE.md: if another agent is mid-merge, wait and retry rather than racing.)

- [ ] **Step 4: Clean up the worktree**

```bash
# Remove the node_modules junction FIRST (never recurse-delete a junction from the worktree side)
rmdir .claude/worktrees/<name>/frontend/node_modules 2>/dev/null || true
git worktree remove .claude/worktrees/<name>
```
Kill any orphaned `vite` process before removal.

- [ ] **Step 5: Push**

```bash
git push origin main
```

- [ ] **Step 6: Manual smoke test (optional, real keys)**

In Settings, enable OpenAlex (+ optional key) and Semantic Scholar. In a general chat, ask "find published papers on retrieval-augmented generation" and confirm cards from each source appear, OA cards open in-app, external cards open externally. This is the only step that needs real network/keys; everything else is verified offline.

---

## Self-Review (run after writing, fix inline)

Done — checked against the spec:

**Spec coverage:**
- OpenAlex router + S2 router + normalized Paper shape → Tasks 2, 3, 1. ✓
- Extended PDF proxy `/api/pdf-url` with SSRF guard + hashed cache → Task 4. ✓
- Frontend api fns (`searchOpenAlex`, `searchSemanticScholar`, `pdfUrlForOa`) → Task 6. ✓
- `buildSearchTools` + dispatch + fallback-to-arxiv on error → Task 7, 8. ✓
- Dynamic general prompt + source-neutral preamble → Task 11. ✓
- PaperCard source badge + dynamic CTA → Task 9. ✓
- 3-way open routing + metadata seeding (fixes arXiv empty-metadata gap) → Task 10. ✓
- Settings "Search sources" section with toggles + keys + external links → Task 12. ✓
- Stable-id scheme (arxiv→doi→source:stub) → Task 7 `resolvePaperId`/`openTarget`. ✓
- Error handling (429 → fallback; OA fetch fail; empty query; SSRF) → Tasks 2/3/4/8. ✓
- Tests: Vitest unit + verify_papershared + E2E driver → Tasks 1/7/13/14. ✓
- Out of scope respected (no rename, no fan-out, websearch.py untouched) — confirmed in plan. ✓

**Placeholder scan:** none — every step has concrete code/commands. ✓

**Type consistency:** `enabledSearchSources()` returns `{openalex, s2}` used identically by `buildSearchTools`, `buildGeneralSystemPrompt`, and `runConversation`'s `enabledSources`. `openTarget` returns the discriminated union consumed by `PaperCard` (`previewable`) and `onOpenPaper` (`target.kind`). `PdfViewer.pdfUrlOverride` is a string built via `pdfUrlForOa` in PaperView. ✓
