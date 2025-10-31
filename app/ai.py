import json
import logging
import time
from typing import Optional, Tuple
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import settings
from . import dashboard as dashboard_mod
from .resilience import get_breaker

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover - SDK not installed yet
    OpenAI = None  # type: ignore


router = APIRouter()
ai_logger = logging.getLogger("ai")


class AITextRequest(BaseModel):
    prompt: str = Field(..., min_length=1, description="Input prompt for AI generation")


class AITextResponse(BaseModel):
    english: str
    vietnamese: str
    model: str
    request_id: str


def _get_client() -> Optional["OpenAI"]:
    if OpenAI is None:
        return None
    if not settings.OPENAI_API_KEY:
        return None
    try:
        return OpenAI(api_key=settings.OPENAI_API_KEY)
    except Exception:
        return None


# Simple in-memory LRU cache for stable, quick repeated responses
_cache: "OrderedDict[Tuple[str, str], Tuple[float, AITextResponse]]" = OrderedDict()


def _cache_get(prompt: str, model: str) -> Optional[AITextResponse]:
    key = (prompt.strip(), model)
    now = time.time()
    if key in _cache:
        ts, value = _cache[key]
        if now - ts <= settings.AI_CACHE_TTL_SECONDS:
            _cache.move_to_end(key)
            return value
        else:
            try:
                del _cache[key]
            except KeyError:
                pass
    return None


def _cache_put(prompt: str, model: str, resp: AITextResponse) -> None:
    key = (prompt.strip(), model)
    _cache[key] = (time.time(), resp)
    _cache.move_to_end(key)
    # trim
    while len(_cache) > settings.AI_CACHE_MAX:
        _cache.popitem(last=False)


@router.post("/generate_ai_text", response_model=AITextResponse)
async def generate_ai_text(payload: AITextRequest, request: Request) -> AITextResponse:
    req_id = getattr(request.state, "request_id", "-")
    ip = request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")
    ua = request.headers.get("user-agent", "?")

    client = _get_client()
    model = settings.AI_MODEL

    # Cache first for stability and speed on repeated prompts
    start_total = time.perf_counter()
    cached = _cache_get(payload.prompt, model)
    if cached:
        ai_logger.info(
            "AI cache_hit | id=%s | ip=%s | ua=%s | model=%s | prompt_len=%s",
            req_id,
            ip,
            ua,
            model,
            len(payload.prompt),
        )
        try:
            dur_ms = int((time.perf_counter() - start_total) * 1000)
            # Record generation for dashboard metrics (include cache hits)
            await dashboard_mod.record_ai_generation(dur_ms)
        except Exception:
            pass
        return cached

    # Instruct the model to return strict JSON for reliable parsing
    system_msg = (
        "You are a helpful assistant. Reply in JSON with keys 'english' and 'vietnamese'. "
        "Keep responses concise and faithful to the user's prompt."
    )
    user_msg = (
        "Generate a helpful response to the following prompt. "
        "Return both an English version and a Vietnamese translation.\n\n"
        f"Prompt: {payload.prompt}"
    )

    def _fallback(reason: str) -> AITextResponse:
        english = f"[Fast fallback] Response to: {payload.prompt}"
        vietnamese = f"[Fast fallback] Phản hồi cho: {payload.prompt}"
        ai_logger.warning(
            "AI fallback used | id=%s | ip=%s | ua=%s | reason=%s",
            req_id,
            ip,
            ua,
            reason,
        )
        return AITextResponse(
            english=english,
            vietnamese=vietnamese,
            model="fallback",
            request_id=req_id,
        )

    try:
        if client is None:
            resp = _fallback("openai_not_configured")
            _cache_put(payload.prompt, model, resp)
            try:
                dur_ms = int((time.perf_counter() - start_total) * 1000)
                await dashboard_mod.record_ai_generation(dur_ms)
            except Exception:
                pass
            return resp

        # Run the OpenAI call in a thread with a tight timeout to keep total < 2s
        start = time.perf_counter()

        # Circuit breaker per-model to fast-fail when upstream is down
        breaker = get_breaker(
            key=f"openai:chat:{model}",
            fail_threshold=settings.AI_CIRCUIT_FAIL_THRESHOLD,
            reset_timeout=float(settings.AI_CIRCUIT_RESET_SEC),
        )

        # Remaining time budget across retries
        total_budget = max(0.5, settings.AI_TIMEOUT_MS / 1000)
        attempts = max(0, settings.AI_MAX_RETRIES) + 1
        last_exc: Optional[BaseException] = None

        for i in range(attempts):
            if not breaker.allow_request():
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ai_circuit_open")

            elapsed = time.perf_counter() - start
            remaining = max(0.3, total_budget - elapsed)

            def _call():
                return client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": user_msg},
                    ],
                    temperature=0.2,
                    max_tokens=200,
                    response_format={"type": "json_object"},
                    timeout=remaining,
                )

            try:
                # Enforce our own budgeted timeout guard as well
                completion = await run_in_threadpool(_call)
                breaker.record_success()
                break
            except Exception as e:
                last_exc = e
                breaker.record_failure()
                if i < attempts - 1:
                    # brief jittered backoff within remaining budget
                    backoff = min(0.25 * (2 ** i), max(0.05, remaining / 3))
                    import asyncio as _asyncio
                    await _asyncio.sleep(backoff)
                    continue
                # out of retries
                raise
        content = completion.choices[0].message.content or "{}"
        data = {}
        try:
            data = json.loads(content)
        except Exception:
            # Attempt to coerce minimal structure
            data = {"english": content, "vietnamese": content}

        english = str(data.get("english", "")).strip()
        vietnamese = str(data.get("vietnamese", "")).strip()
        if not english or not vietnamese:
            # Ensure bilingual output even if model deviates
            english = english or content
            vietnamese = vietnamese or content

        ai_logger.info(
            "AI success | id=%s | ip=%s | ua=%s | model=%s | prompt_len=%s | out_en=%s | out_vi=%s",
            req_id,
            ip,
            ua,
            model,
            len(payload.prompt),
            min(len(english), 120),
            min(len(vietnamese), 120),
        )
        resp = AITextResponse(
            english=english,
            vietnamese=vietnamese,
            model=model,
            request_id=req_id,
        )
        _cache_put(payload.prompt, model, resp)
        # Budget check (defensive): if we somehow exceeded, still return; outer infra controls SLA.
        _ = time.perf_counter() - start
        try:
            dur_ms = int((time.perf_counter() - start_total) * 1000)
            await dashboard_mod.record_ai_generation(dur_ms)
        except Exception:
            pass
        return resp
    except HTTPException:
        raise
    except Exception as e:
        # For stability and latency: fallback instead of erroring out
        ai_logger.exception(
            "AI error | id=%s | ip=%s | ua=%s | model=%s | err=%s",
            req_id,
            ip,
            ua,
            model,
            repr(e),
        )
        resp = _fallback("error_fallback")
        _cache_put(payload.prompt, model, resp)
        try:
            dur_ms = int((time.perf_counter() - start_total) * 1000)
            await dashboard_mod.record_ai_generation(dur_ms)
        except Exception:
            pass
        return resp


# Simple Server-Sent Events stream that emits the final result in small chunks
@router.get("/generate_ai_text_stream")
async def generate_ai_text_stream(prompt: str, request: Request):
    """
    Streams the generated content as text/event-stream for realtime UI updates.
    This endpoint does not stream from the model; it generates once (with caching)
    then emits the english and vietnamese fields incrementally.
    """
    from fastapi.responses import StreamingResponse
    import asyncio

    async def _gen():
        try:
            resp = await generate_ai_text(AITextRequest(prompt=prompt), request)
            # Announce start
            start_event = json.dumps({
                "model": resp.model,
                "request_id": resp.request_id,
                "status": "started",
            })
            yield f"event: start\ndata: {start_event}\n\n"

            # Helper to chunk a string for smoother UI updates
            def chunk_text(text: str, size: int = 40):
                for i in range(0, len(text), size):
                    yield text[i : i + size]

            # Stream English
            for part in chunk_text(resp.english):
                yield f"event: delta\ndata: {json.dumps({'lang':'english','text': part})}\n\n"
                await asyncio.sleep(0.02)

            yield f"event: section_end\ndata: {json.dumps({'lang':'english'})}\n\n"

            # Stream Vietnamese
            for part in chunk_text(resp.vietnamese):
                yield f"event: delta\ndata: {json.dumps({'lang':'vietnamese','text': part})}\n\n"
                await asyncio.sleep(0.02)

            yield f"event: section_end\ndata: {json.dumps({'lang':'vietnamese'})}\n\n"

            # Done
            yield f"event: done\ndata: {json.dumps({'status':'completed'})}\n\n"
        except Exception as e:  # pragma: no cover - streaming path
            ai_logger.exception("SSE stream error: %s", repr(e))
            err = json.dumps({"error": "stream_failed"})
            yield f"event: error\ndata: {err}\n\n"

    return StreamingResponse(_gen(), media_type="text/event-stream")
