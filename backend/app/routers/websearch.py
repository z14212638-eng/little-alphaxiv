"""Web search proxy via the anysearch MCP server (Streamable HTTP transport).

anysearch is exposed as an HTTP MCP server (default https://api.anysearch.com/mcp).
We speak JSON-RPC 2.0 to it directly: a stateless `tools/call` for the `search`
tool returns markdown-formatted results, which we parse into {title,url,snippet}
objects so the LLM gets clean structured data it can cite.

Configuration (env):
  - ANYSEARCH_API_KEY  — required to enable web search. Reuses the same env var
    the user's Claude Code MCP config already references, so a backend started
    from the same shell inherits it with no extra setup.
  - LAX_ANYSEARCH_URL  — optional override of the MCP endpoint URL.

Without a key the endpoint returns `configured:false` (HTTP 200) so the model
can fall back to the academic search tools (search_arxiv / search_openalex /
search_semantic_scholar) instead of hard-failing the tool-calling loop.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()

_DEFAULT_ANYSEARCH_URL = "https://api.anysearch.com/mcp"
_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)

# One "### N. <title>\n- **URL**: <url>\n- <snippet>..." block, up to the next
# numbered heading or end of text.
_BLOCK = re.compile(r"###\s*\d+\.\s*(.*?)(?=\n###\s*\d+\.|\Z)", re.DOTALL)
_URL_LINE = re.compile(r"\*{0,2}URL\*{0,2}\s*[:：]\s*(\S+)")


def _get_config() -> tuple[str, str]:
    """Return (api_key, mcp_url). api_key is "" when web search is not configured."""
    key = os.environ.get("ANYSEARCH_API_KEY", "").strip()
    url = (
        os.environ.get("LAX_ANYSEARCH_URL")
        or os.environ.get("ANYSEARCH_MCP_URL")
        or _DEFAULT_ANYSEARCH_URL
    ).strip()
    return key, url


def parse_anysearch_markdown(md: str) -> list[dict[str, Any]]:
    """Parse anysearch's "## Search Results" markdown into result dicts.

    Each block looks like::

        ### 1. Some Title - Source
        - **URL**: https://example.org/...
        - Snippet text spanning the rest of the block.

    Returns a list of ``{rank, title, url, snippet}``. Defensive: if the format
    doesn't match (anysearch changed its rendering, or returned an error string),
    returns a single item carrying the raw text so the LLM still sees the data
    instead of a silent empty array.
    """
    if not md or not md.strip():
        return []
    blocks = list(_BLOCK.finditer(md))
    if not blocks:
        return [{"rank": 1, "title": "web search results", "url": "", "snippet": md.strip()}]
    out: list[dict[str, Any]] = []
    for i, m in enumerate(blocks, start=1):
        body = m.group(1).strip()
        lines = body.split("\n")
        title = lines[0].strip()
        url = ""
        snippet_parts: list[str] = []
        for line in lines[1:]:
            s = line.strip()
            if not s:
                continue
            um = _URL_LINE.search(s)
            if um and not url:
                url = um.group(1)
            else:
                snippet_parts.append(s.lstrip("- ").strip())
        out.append(
            {
                "rank": i,
                "title": title or "",
                "url": url,
                "snippet": " ".join(snippet_parts).strip(),
            }
        )
    return out


def _extract_markdown(rpc: dict[str, Any]) -> str:
    """Pull the text payload out of an MCP ``tools/call`` JSON-RPC response.

    A successful response is ``{"result": {"content": [{"type":"text","text": "..."}]}}``.
    Returns "" if no text content is present.
    """
    result = rpc.get("result") or {}
    content = result.get("content") or []
    if not isinstance(content, list):
        return ""
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text"):
            return str(item["text"])
    return ""


def _parse_rpc_response(resp: httpx.Response) -> dict[str, Any]:
    """Decode a JSON-RPC response, handling both plain-JSON and SSE bodies.

    Stateless ``tools/call`` returns ``application/json`` today, but the MCP
    Streamable HTTP transport may also answer with ``text/event-stream``; in that
    case the JSON-RPC payload is the last ``data:`` line.
    """
    ct = resp.headers.get("content-type", "")
    if "text/event-stream" in ct:
        data_lines = [
            ln[5:].strip()
            for ln in resp.text.splitlines()
            if ln.startswith("data:")
        ]
        if not data_lines:
            raise RuntimeError("anysearch returned an empty SSE stream")
        return json.loads(data_lines[-1])
    return resp.json()


async def _post_jsonrpc(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """POST one JSON-RPC request to the anysearch MCP endpoint, return parsed JSON.

    ``transport`` is for tests (httpx.MockTransport); production leaves it None.
    """
    async with httpx.AsyncClient(
        timeout=_TIMEOUT, follow_redirects=True, transport=transport
    ) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(
            f"anysearch returned status {resp.status_code}: {resp.text[:300]}"
        )
    try:
        return _parse_rpc_response(resp)
    except (ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"anysearch json error: {exc}") from exc


def _fallback(message: str, q: str) -> JSONResponse:
    """Return a configured-but-empty response so the LLM can fall back gracefully."""
    return JSONResponse(
        content={
            "configured": True,
            "message": message,
            "query": q,
            "results": [],
        }
    )


@router.get("/websearch")
async def websearch(
    q: str = Query(..., description="general web search query"),
    max_results: int = Query(8, ge=1, le=10),
) -> Any:
    key, url = _get_config()
    if not key:
        return JSONResponse(
            content={
                "configured": False,
                "message": (
                    "web search (anysearch) is not configured on the backend — set the "
                    "ANYSEARCH_API_KEY env var. Falling back to academic search tools."
                ),
                "query": q,
                "results": [],
            }
        )

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "search",
            "arguments": {"query": q, "max_results": max_results},
        },
    }

    try:
        rpc = await _post_jsonrpc(url, headers, payload)
    except (httpx.RequestError, RuntimeError) as exc:
        return _fallback(
            f"web_search failed ({exc}); try search_arxiv / search_openalex / search_semantic_scholar.",
            q,
        )

    err = rpc.get("error") if isinstance(rpc, dict) else None
    if err:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        return _fallback(f"web_search error: {msg}", q)

    md = _extract_markdown(rpc)
    results = parse_anysearch_markdown(md)
    return JSONResponse(
        content={
            "configured": True,
            "query": q,
            "results": results,
            "total": len(results),
        }
    )
