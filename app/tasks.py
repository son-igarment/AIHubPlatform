import asyncio
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


router = APIRouter()
log = logging.getLogger("automation")


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


async def _notify_telegram_done(info: Dict[str, Any], run_id: str) -> None:
    name = info.get("name") or info.get("task_id") or "(unknown)"
    assignees = info.get("assignees") or []
    who = ", ".join(map(str, assignees)) if assignees else "-"
    msg = (
        f"✅ Task DONE → NEXT\n"
        f"run_id: `{run_id}`\n"
        f"task: {name}\n"
        f"id: {info.get('task_id')} | list: {info.get('list_id')}\n"
        f"assignees: {who}\n"
        f"updated: {info.get('date_updated') or _now_iso()}"
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


def _update_sheet(info: Dict[str, Any], run_id: str) -> None:
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
    }
    _post_json(url, payload, run_id)


def _start_next_task(info: Dict[str, Any], run_id: str) -> Optional[Dict[str, Any]]:
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
    return _post_json(url, payload, run_id)


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

    dedup_key, info = _normalize_clickup(payload)
    if not info.get("task_id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid payload: missing task id")

    # Only process terminal state done
    status_str = (info.get("status") or "").lower() if isinstance(info.get("status"), str) else info.get("status")
    if status_str not in {"done", "complete", "completed"}:
        return {"ok": True, "ignored": True}

    # Idempotency guard
    key = dedup_key or json.dumps({"tid": info.get("task_id"), "status": status_str}, sort_keys=True)
    if not _remember(key):
        return {"ok": True, "duplicate": True}

    run_id = str(uuid.uuid4())
    log.info("Task DONE received: task_id=%s status=%s run_id=%s", info.get("task_id"), status_str, run_id)

    # Fire-and-forget Telegram to keep latency low
    try:
        asyncio.create_task(_notify_telegram_done(info, run_id))
    except RuntimeError:
        # Not in event loop context; fallback
        await _notify_telegram_done(info, run_id)

    # Sheets update + next task trigger (sync HTTP calls; brief timeouts)
    try:
        _update_sheet(info, run_id)
    except Exception:
        log.exception("Sheet update failed (run_id=%s)", run_id)

    next_resp = None
    try:
        next_resp = _start_next_task(info, run_id)
    except Exception:
        log.exception("start_next_task failed (run_id=%s)", run_id)

    return {"ok": True, "run_id": run_id, "next": next_resp or None}
