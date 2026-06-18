"""Models list proxy — forwards GET /v1/models to the user's provider.

OpenAI-compatible providers (ppio, OpenAI, etc.) expose a /models endpoint
that lists available models. We proxy it so the frontend can dynamically
populate a model dropdown without hardcoding model IDs.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()

_TIMEOUT = httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0)


@router.get("/models")
async def list_models(request: Request) -> JSONResponse:
    """Proxy GET /models to the user-configured OpenAI-compatible endpoint.

    Query params:
        base_url: e.g. https://api.ppio.com/openai/v1
        api_key:  Bearer token
    """
    base_url = request.query_params.get("base_url")
    api_key = request.query_params.get("api_key")

    if not base_url:
        raise HTTPException(status_code=400, detail="base_url is required")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")

    target = base_url.strip().rstrip("/") + "/models"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }

    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            resp = await client.get(target, headers=headers)
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=502, detail=f"upstream request error: {exc}"
            ) from exc

    if resp.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"upstream returned {resp.status_code}: {resp.text[:300]}",
        )

    return JSONResponse(content=resp.json(), status_code=resp.status_code)
