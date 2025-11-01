import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, Optional, Set, Tuple

import requests
from fastapi import APIRouter, Header, HTTPException, Request, status

from .config import settings
from .resilience import resilient_request, CircuitOpenError
from .notifications import send_telegram_message
from .automation import automation_scheduler


router = APIRouter()
log = logging.getLogger("automation")
flow_log = logging.getLogger("automation.flow")


# Simple in-memory idempotency guard to avoid loops/duplicates
_seen_keys: Set[str] = set()
_seen_queue: Deque[str] = deque(maxlen=1024)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _remember(key: str) -> bool:
    """Remember a key; return True if it was new, False if already seen."""
    if key in _seen_keys:
        return False
    _seen_keys.add(key)
    _seen_queue.append(key)
    # Drop evicted keys to keep memory bounded
    while len(_seen_keys) > _seen_queue.maxlen:
        try:
            old = _seen_queue.popleft()
            _seen_keys.discard(old)
        except IndexError:
            break
    return True


def _normalize_clickup(payload: Dict[str, Any]) -> Tuple[Optional[str], Dict[str, Any]]:
    """Extract task info from ClickUp-style webhook payload.

    Returns (dedup_key, info_dict)
    """
    # ClickUp task payloads typically provide task fields directly or under 'task' key
    task = payload.get("task") if isinstance(payload.get("task"), dict) else payload
    task_id = str(task.get("id")) if task and task.get("id") is not None else None
    status = None
    if task:
        # status can be a string or object with 'status'
        st = task.get("status")
        if isinstance(st, dict):
            status = st.get("status") or st.get("type") or st.get("name")
        elif isinstance(st, str):
            status = st
    date_updated = str(task.get("date_updated")) if task and task.get("date_updated") else None
    name = task.get("name") if task else None
    list_id = task.get("list") if task else None
    if isinstance(list_id, dict):
        list_id = list_id.get("id")
    assignees = task.get("assignees") if task else None
    if isinstance(assignees, list):
        assignees = [a.get("username") or a.get("email") or a.get("id") for a in assignees]

    info = {
        "task_id": task_id,
        "status": status.lower() if isinstance(status, str) else status,
        "date_updated": date_updated,
        "name": name,
        "list_id": list_id,
        "assignees": assignees,
        "raw": payload,
    }

    # Build a conservative dedup key
    key_parts = [p for p in (task_id, info["status"], date_updated) if p]
    dedup_key = ":".join(key_parts) if key_parts else None
    return dedup_key, info


async def _notify_telegram_done(info: Dict[str, Any], run_id: str, next_task_id: Optional[str] = None) -> None:
    name = info.get("name") or info.get("task_id") or "(unknown)"
    assignees = info.get("assignees") or []
    who = ", ".join(map(str, assignees)) if assignees else "-"
    next_line = f"next: {next_task_id}" if next_task_id else "next: -"
    msg = (
        f"✅ Task DONE → NEXT\n"
        f"run_id: `{run_id}`\n"
        f"task: {name}\n"
        f"id: {info.get('task_id')} | list: {info.get('list_id')}\n"
        f"assignees: {who}\n"
        f"updated: {info.get('date_updated') or _now_iso()}\n"
        f"{name} → {next_task_id or '-'}\n"
        f"{next_line}"
    )
    try:
        await send_telegram_message(msg)
    except Exception:  # keep webhook fast
        logging.getLogger(__name__).exception("Telegram notify failed")


def _post_json(url: str, data: Dict[str, Any], run_id: str, timeout: Optional[int] = None) -> Optional[Dict[str, Any]]:
    headers = {"Content-Type": "application/json", "X-Run-Id": run_id, "User-Agent": "AIHubWebhook/1.0"}
    timeout_sec = float(timeout or settings.HTTP_TIMEOUT_SEC)
    try:
        resp = resilient_request(
            "POST",
            url,
            timeout=timeout_sec,
            retries=settings.HTTP_MAX_RETRIES,
            backoff_base_ms=settings.HTTP_BACKOFF_BASE_MS,
            circuit_key=f"post:{url}",
            circuit_fail_threshold=settings.HTTP_CIRCUIT_FAIL_THRESHOLD,
            circuit_reset_sec=settings.HTTP_CIRCUIT_RESET_SEC,
            json=data,
            headers=headers,
        )
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {"status": resp.status_code, "text": resp.text}
    except CircuitOpenError:
        log.warning("Circuit open for %s; skipping POST", url)
        return None
    except Exception:
        log.exception("POST %s failed", url)
        return None


def _update_sheet(info: Dict[str, Any], run_id: str, next_task_id: Optional[str]) -> None:
    url = os.getenv("SHEETS_WEBHOOK_URL") or os.getenv("GOOGLE_SHEETS_WEBHOOK")
    if not url:
        log.debug("Sheets webhook not configured; skip update")
        return
    payload = {
        "event": "task_done",
        "run_id": run_id,
        "task_id": info.get("task_id"),
        "name": info.get("name"),
        "status": info.get("status"),
        "list_id": info.get("list_id"),
        "date_updated": info.get("date_updated") or _now_iso(),
        "next_task_id": next_task_id,
    }
    _post_json(url, payload, run_id)


def _resolve_next_task_id(info: Dict[str, Any], run_id: str) -> Optional[str]:
    next_from_payload = info.get("next_task_id") or info.get("next_id")
    if next_from_payload:
        return str(next_from_payload)
    lookup_url = os.getenv("NEXT_TASK_LOOKUP_URL")
    if not lookup_url:
        return None
    resp = _post_json(lookup_url, {"task_id": info.get("task_id"), "run_id": run_id}, run_id)
    if not resp:
        return None
    if isinstance(resp, dict):
        candidate = resp.get("next_task_id") or resp.get("id")
        if candidate:
            return str(candidate)
    return None


def _start_next_task(info: Dict[str, Any], run_id: str, next_task_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Trigger the next task via configurable endpoint, keeping it decoupled.

    Behavior priority:
    - If NEXT_TASK_URL configured -> POST there with context and run_id
    - Else no-op with log
    """
    url = os.getenv("NEXT_TASK_URL") or os.getenv("START_NEXT_TASK_URL")
    if not url:
        log.info("NEXT_TASK_URL not configured; start_next_task skipped (run_id=%s)", run_id)
        return None
    payload = {
        "action": "start_next_task",
        "run_id": run_id,
        "context": {
            "prev_task_id": info.get("task_id"),
            "prev_task_name": info.get("name"),
            "prev_status": info.get("status"),
            "list_id": info.get("list_id"),
        },
    }
    if next_task_id:
        payload["next_task_id"] = next_task_id
    return _post_json(url, payload, run_id)


def _label_task_aihub_auto(task_id: Optional[str], run_id: str) -> Optional[Dict[str, Any]]:
    if not task_id:
        flow_log.info("label_skip | run_id=%s | reason=missing_task_id", run_id)
        return None
    token = os.getenv("CLICKUP_API_TOKEN") or os.getenv("TASK_API_TOKEN")
    if not token:
        flow_log.info("label_skip | run_id=%s | task_id=%s | reason=missing_token", run_id, task_id)
        return None
    base = os.getenv("CLICKUP_API_BASE", "https://api.clickup.com/api/v2").rstrip("/")
    url = f"{base}/task/{task_id}/tag"
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
        "User-Agent": "AIHubAutomation/1.0",
    }
    payload = {"name": "AIHubAuto"}
    try:
        resp = resilient_request(
            "POST",
            url,
            timeout=float(settings.HTTP_TIMEOUT_SEC),
            retries=settings.HTTP_MAX_RETRIES,
            backoff_base_ms=settings.HTTP_BACKOFF_BASE_MS,
            circuit_key=f"clickup:label:{task_id}",
            circuit_fail_threshold=settings.HTTP_CIRCUIT_FAIL_THRESHOLD,
            circuit_reset_sec=settings.HTTP_CIRCUIT_RESET_SEC,
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        try:
            data = resp.json()
        except ValueError:
            data = {"status": resp.status_code}
        flow_log.info("label_applied | run_id=%s | task_id=%s | status=%s", run_id, task_id, resp.status_code)
        return data
    except CircuitOpenError:
        flow_log.warning("label_failed | run_id=%s | task_id=%s | reason=circuit_open", run_id, task_id)
    except Exception:
        flow_log.exception("label_failed | run_id=%s | task_id=%s", run_id, task_id)
    return None


def _log_flow_event(event: str, run_id: str, **details: Any) -> None:
    payload = {"event": event, "run_id": run_id, **details}
    flow_log.info("flow_event | %s", json.dumps(payload, ensure_ascii=False, default=str))


def _verify_clickup_signature(raw_body: bytes, request: Request) -> None:
    secret = settings.CLICKUP_WEBHOOK_SECRET or ""
    if not secret:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Webhook secret missing")
    signature = None
    for header in ("x-clickup-signature", "x-signature", "x-hub-signature"):
        value = request.headers.get(header)
        if value:
            signature = value.strip()
            break
    if not signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature header")
    if signature.startswith("sha256="):
        signature = signature.split("=", 1)[1]
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature.lower(), expected.lower()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")


async def _process_clickup_event(payload: Dict[str, Any], request: Request) -> Dict[str, Any]:
    dedup_key, info = _normalize_clickup(payload)
    if not info.get("task_id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload: missing task id")

    status_str = (info.get("status") or "").lower() if isinstance(info.get("status"), str) else info.get("status")
    if status_str not in {"done", "complete", "completed"}:
        return {"ok": True, "ignored": True}

    key = dedup_key or json.dumps({"tid": info.get("task_id"), "status": status_str}, sort_keys=True)
    if not _remember(key):
        return {"ok": True, "duplicate": True}

    run_id = str(uuid.uuid4())
    log.info("Task DONE received: task_id=%s status=%s run_id=%s", info.get("task_id"), status_str, run_id)
    _log_flow_event("task_done", run_id, task_id=info.get("task_id"), status=status_str, name=info.get("name"))

    next_task_id = _resolve_next_task_id(info, run_id)
    if next_task_id:
        _log_flow_event("next_task_resolved", run_id, next_task_id=next_task_id)

    label_resp = None
    try:
        label_resp = _label_task_aihub_auto(info.get("task_id"), run_id)
    except Exception:
        log.exception("apply_label failed (run_id=%s)", run_id)
    if label_resp is not None:
        _log_flow_event("label_applied", run_id, task_id=info.get("task_id"), label="AIHubAuto")

    next_resp = None
    try:
        next_resp = _start_next_task(info, run_id, next_task_id)
    except Exception:
        log.exception("start_next_task failed (run_id=%s)", run_id)
    if next_resp is not None:
        _log_flow_event("next_task_triggered", run_id, response=next_resp)

    try:
        _update_sheet(info, run_id, next_task_id)
    except Exception:
        log.exception("Sheet update failed (run_id=%s)", run_id)

    async def _send_notification() -> None:
        await _notify_telegram_done(info, run_id, next_task_id)

    try:
        asyncio.create_task(_send_notification())
    except RuntimeError:
        await _notify_telegram_done(info, run_id, next_task_id)

    scheduler_result = None
    try:
        asyncio.create_task(automation_scheduler.run_now(origin="clickup_webhook"))
        _log_flow_event("scheduler_dispatched", run_id, origin="clickup_webhook")
    except RuntimeError:
        scheduler_result = await automation_scheduler.run_now(origin="clickup_webhook")
        _log_flow_event("scheduler_ran_inline", run_id, origin="clickup_webhook", status=scheduler_result.status if scheduler_result else None)
    except Exception:
        flow_log.exception("scheduler_dispatch_failed | run_id=%s", run_id)

    response_payload = {
        "ok": True,
        "run_id": run_id,
        "next": next_resp or None,
        "next_task_id": next_task_id,
    }
    return response_payload


@router.post("/task/")
async def clickup_webhook(
    payload: Dict[str, Any],
    request: Request,
    x_webhook_token: Optional[str] = Header(default=None, alias="X-Webhook-Token"),
):
    """Accept ClickUp webhook events and run the Done ⇒ Next flow.

    - Idempotent by dedup key (task_id:status:date_updated)
    - Only reacts to status == "done"
    - Produces a run_id and fans out to Sheet + Telegram + start_next_task()
    """
    # Optional shared secret
    expected = os.getenv("TASK_WEBHOOK_TOKEN") or os.getenv("WEBHOOK_TOKEN")
    if expected and (not x_webhook_token or x_webhook_token != expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook token")

    return await _process_clickup_event(payload, request)


@router.post("/task/webhook")
async def clickup_webhook_hmac(request: Request):
    raw = await request.body()
    _verify_clickup_signature(raw, request)
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON payload") from exc
    return await _process_clickup_event(payload, request)
