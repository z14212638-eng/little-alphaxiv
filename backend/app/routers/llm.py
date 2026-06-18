"""LLM proxy — passthrough to an OpenAI-compatible /chat/completions endpoint.

Stateless: base_url + api_key arrive per-request in the JSON body (the browser
holds them in localStorage). We forward the payload verbatim and stream the
upstream SSE response back to the client byte-for-byte.
"""
from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

router = APIRouter()

# OpenAI-compatible chat completions path appended to the user's base_url.
_CHAT_PATH = "/chat/completions"
# Keep the upstream connection alive across long streaming turns.
_TIMEOUT = httpx.Timeout(connect=15.0, read=300.0, write=60.0, pool=15.0)


class ProxyRequest(BaseException):
    """Internal sentinel — not used (kept for clarity)."""


def _resolve_target(base_url: str) -> str:
    base = base_url.strip().rstrip("/")
    if not base:
        raise HTTPException(status_code=400, detail="base_url is required")
    return base + _CHAT_PATH


@router.post("/llm")
async def llm_proxy(request: Request) -> Any:
    """Forward a chat-completion request to the user-configured OpenAI-compatible endpoint.

    Body shape:
        {
          "base_url": "https://api.openai.com/v1",
          "api_key": "sk-...",
          "payload": { ...full OpenAI chat completion body incl. messages, tools, stream }
        }
    """
    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc

    base_url = body.get("base_url")
    api_key = body.get("api_key")
    payload = body.get("payload")

    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload object is required")

    target = _resolve_target(base_url) if base_url else None
    if target is None:
        raise HTTPException(status_code=400, detail="base_url is required")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        # Some OpenAI-compatible gateways require an explicit accept for SSE.
        "Accept": "text/event-stream" if payload.get("stream") else "application/json",
    }

    want_stream = bool(payload.get("stream"))

    if not want_stream:
        # Non-streaming: forward and return JSON.
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                resp = await client.post(target, headers=headers, json=payload)
            except httpx.RequestError as exc:
                raise HTTPException(
                    status_code=502, detail=f"upstream request error: {exc}"
                ) from exc
        if resp.status_code >= 400:
            return JSONResponse(content=resp.json(), status_code=resp.status_code)
        return JSONResponse(content=resp.json(), status_code=resp.status_code)

    # Streaming: pipe upstream SSE straight through.
    async def stream_upstream() -> Any:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            try:
                async with client.stream(
                    "POST", target, headers=headers, json=payload
                ) as resp:
                    if resp.status_code >= 400:
                        text = await resp.aread()
                        # Surface upstream error as an SSE error event so the
                        # client can render it instead of silently dying.
                        err = {
                            "error": True,
                            "status": resp.status_code,
                            "body": text.decode("utf-8", errors="replace"),
                        }
                        yield f"data: {json.dumps(err)}\n\n"
                        yield "data: [DONE]\n\n"
                        return
                    async for chunk in resp.aiter_raw():
                        if chunk:
                            yield chunk
            except httpx.RequestError as exc:
                err = {"error": True, "message": f"upstream stream error: {exc}"}
                yield f"data: {json.dumps(err)}\n\n"
                yield "data: [DONE]\n\n"

    return StreamingResponse(
        stream_upstream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
