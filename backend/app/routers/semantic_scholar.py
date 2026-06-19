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
