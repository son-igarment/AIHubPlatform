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


class AILatencyPoint(BaseModel):
    ts: str
    ms: int


class AIStats(BaseModel):
    total_generations: int
    avg_similarity: float
    similarity_count: int
    p50_latency_ms: int
    p95_latency_ms: int
    avg_latency_ms: int
    updated_at: str

@dataclass
class _State:
    # metrics
    points: Deque[MetricPoint]
    max_points: int
    # summaries
    tasks: TaskSummary
    reports: ReportSummary
    # ai stats
    ai_total_generations: int
    ai_similarity_samples: Deque[float]
    ai_latencies: Deque[AILatencyPoint]
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
    ai_total_generations=0,
    ai_similarity_samples=deque(maxlen=400),
    ai_latencies=deque(maxlen=400),
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


def _percentile(sorted_vals: List[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    d0 = sorted_vals[f] * (c - k)
    d1 = sorted_vals[c] * (k - f)
    return d0 + d1


async def record_ai_generation(duration_ms: int) -> None:
    now = _now_iso()
    async with state.lock:
        state.ai_total_generations += 1
        state.ai_latencies.append(AILatencyPoint(ts=now, ms=max(0, int(duration_ms))))
    # Broadcast lightweight tick for realtime
    try:
        await _broadcast({
            "type": "ai_tick",
            "ai": await get_ai_stats_raw(),
            "latency": {"ts": now, "ms": max(0, int(duration_ms))},
        })
    except Exception:
        pass


async def record_similarity(score: float) -> None:
    async with state.lock:
        # clamp to [0,1] for safety
        s = max(0.0, min(1.0, float(score)))
        state.ai_similarity_samples.append(s)
    try:
        await _broadcast({
            "type": "ai_similarity",
            "ai": await get_ai_stats_raw(),
            "similarity": s,
        })
    except Exception:
        pass


async def get_ai_stats_raw() -> Dict[str, object]:
    async with state.lock:
        sims = list(state.ai_similarity_samples)
        lats = [p.ms for p in state.ai_latencies]
        avg_sim = (sum(sims) / len(sims)) if sims else 0.0
        lats_sorted = sorted(lats)
        p50 = int(round(_percentile(lats_sorted, 0.5))) if lats_sorted else 0
        p95 = int(round(_percentile(lats_sorted, 0.95))) if lats_sorted else 0
        avg_lat = int(round((sum(lats) / len(lats)))) if lats else 0
        return {
            "total_generations": state.ai_total_generations,
            "avg_similarity": round(avg_sim, 3),
            "similarity_count": len(sims),
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "avg_latency_ms": avg_lat,
            "updated_at": _now_iso(),
        }


@router.get("/api/v1/ai/stats", response_model=AIStats)
async def get_ai_stats() -> AIStats:
    data = await get_ai_stats_raw()
    return AIStats(**data)  # type: ignore[arg-type]


@router.get("/api/v1/ai/history")
async def get_ai_history(limit: int = 120) -> Dict[str, object]:
    async with state.lock:
        lat = list(state.ai_latencies)[-max(1, min(limit, state.ai_latencies.maxlen or 120)) :]
        sims = list(state.ai_similarity_samples)[-max(1, min(limit, state.ai_similarity_samples.maxlen or 120)) :]
        return {
            "latencies": [p.model_dump() for p in lat],
            "similarities": sims,
        }


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
                "ai": await get_ai_stats_raw(),
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

