"""Web search proxy via the user's existing anysearch MCP server.

Wired in fully at Step 6. For now this is a placeholder that returns a
'not-configured' message so the app imports and the route exists. The real
implementation will spawn/attach an MCP client (stdio or HTTP transport) to
the user's anysearch server and proxy `search` tool calls here.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/websearch")
async def websearch(
    q: str = Query(..., description="general web search query"),
    max_results: int = Query(8, ge=1, le=10),
) -> Any:
    # Step 6 will replace this with a real MCP client call to anysearch.
    return JSONResponse(
        content={
            "configured": False,
            "message": (
                "anysearch MCP not wired yet (Step 6). arxiv search via "
                "/api/search is available now."
            ),
            "query": q,
            "results": [],
        }
    )
