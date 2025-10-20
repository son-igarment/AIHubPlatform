import json
import logging
import time
from typing import Optional, Tuple
from collections import OrderedDict

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .config import settings

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
            return resp

        # Run the OpenAI call in a thread with a tight timeout to keep total < 2s
        start = time.perf_counter()
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
                timeout=max(0.5, settings.AI_TIMEOUT_MS / 1000 - 0.2),
            )

        # Await with budgeted time (e.g., 1.8s default)
        completion = await run_in_threadpool(_call)
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
        return resp
