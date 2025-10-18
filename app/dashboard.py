import asyncio
import json
import random
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel


router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskSummary(BaseModel):
    total: int
    completed: int
    pending: int
    failed: int
    updated_at: str


class ReportSummary(BaseModel):
    total: int
    delivered_today: int
    pending_review: int
    failed: int
    updated_at: str


class MetricPoint(BaseModel):
    ts: str
    value: int


@dataclass
class _State:
    # metrics
    points: Deque[MetricPoint]
    max_points: int
    # summaries
    tasks: TaskSummary
    reports: ReportSummary
    # concurrency
    lock: asyncio.Lock
    # websocket clients
    clients: List[WebSocket]
    # control
    running: bool


state = _State(
    points=deque(maxlen=300),
    max_points=300,
    tasks=TaskSummary(total=100, completed=72, pending=24, failed=4, updated_at=_now_iso()),
    reports=ReportSummary(total=40, delivered_today=18, pending_review=20, failed=2, updated_at=_now_iso()),
    lock=asyncio.Lock(),
    clients=[],
    running=False,
)


async def _broadcast(payload: dict) -> None:
    stale: List[WebSocket] = []
    for ws in list(state.clients):
        try:
            await ws.send_text(json.dumps(payload))
        except Exception:
            stale.append(ws)
    for ws in stale:
        try:
            state.clients.remove(ws)
        except ValueError:
            pass


async def _metrics_loop():
    # simple synthetic metric generator + light drift of summaries
    base = 50
    val = base
    state.running = True
    while state.running:
        await asyncio.sleep(1)
        jitter = random.randint(-5, 6)
        val = max(0, min(200, val + jitter))
        pt = MetricPoint(ts=_now_iso(), value=val)
        async with state.lock:
            state.points.append(pt)
            # light drift on summaries
            if random.random() < 0.7:
                delta_done = random.choice([0, 1])
                delta_fail = 1 if random.random() < 0.05 else 0
                completed = min(state.tasks.total, state.tasks.completed + delta_done)
                failed = min(state.tasks.total - completed, state.tasks.failed + delta_fail)
                pending = max(0, state.tasks.total - completed - failed)
                state.tasks = TaskSummary(
                    total=state.tasks.total,
                    completed=completed,
                    pending=pending,
                    failed=failed,
                    updated_at=_now_iso(),
                )
                # reports drift
                delivered_today = min(state.reports.total, state.reports.delivered_today + (1 if random.random() < 0.3 else 0))
                pending_review = max(0, state.reports.total - delivered_today - state.reports.failed)
                state.reports = ReportSummary(
                    total=state.reports.total,
                    delivered_today=delivered_today,
                    pending_review=pending_review,
                    failed=state.reports.failed,
                    updated_at=_now_iso(),
                )
            payload = {
                "type": "metric",
                "point": pt.model_dump(),
                "tasks": state.tasks.model_dump(),
                "reports": state.reports.model_dump(),
            }
        await _broadcast(payload)


@router.get("/api/v1/tasks/summary", response_model=TaskSummary)
async def get_task_summary() -> TaskSummary:
    async with state.lock:
        return state.tasks


@router.get("/api/v1/reports/summary", response_model=ReportSummary)
async def get_report_summary() -> ReportSummary:
    async with state.lock:
        return state.reports


@router.get("/api/v1/metrics/history")
async def get_metric_history(limit: int = 120) -> Dict[str, List[MetricPoint]]:
    async with state.lock:
        data = list(state.points)[-max(1, min(limit, state.max_points)) :]
        return {"points": [p.model_dump() for p in data]}


@router.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket):
    await ws.accept()
    state.clients.append(ws)
    try:
        # send snapshot on connect
        async with state.lock:
            snapshot = {
                "type": "snapshot",
                "points": [p.model_dump() for p in state.points],
                "tasks": state.tasks.model_dump(),
                "reports": state.reports.model_dump(),
            }
        await ws.send_text(json.dumps(snapshot))
        # keep alive (no need to receive messages for now)
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        try:
            state.clients.remove(ws)
        except ValueError:
            pass


async def ensure_loop_started():
    if not state.running:
        # prefill a short history for nicer initial chart
        async with state.lock:
            if not state.points:
                base = 50
                for i in range(30, 0, -1):
                    state.points.append(MetricPoint(ts=_now_iso(), value=max(0, base + random.randint(-10, 10))))
        asyncio.create_task(_metrics_loop())

