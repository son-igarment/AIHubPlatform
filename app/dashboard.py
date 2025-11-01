import asyncio
import json
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import modules as modules_core
from .security import get_demo_password_hint, get_user_statistics
from .config import settings


router = APIRouter()

# Simple in-memory TTL caches for metrics endpoints (reduce load under spikes)
_ai_stats_cache: Dict[str, object] = {"ts": 0.0, "val": None}
_ai_history_cache: Dict[str, object] = {"ts": 0.0, "args": None, "val": None}
_metrics_history_cache: Dict[str, object] = {"ts": 0.0, "args": None, "val": None}
_insight_cache: Dict[str, object] = {"ts": 0.0, "val": None}


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


class ModuleState(BaseModel):
    name: str
    enabled: bool


class ModulesSummary(BaseModel):
    total: int
    enabled: int
    disabled: int


class RequestPerMinute(BaseModel):
    minute: str
    count: int


class LatencyPerMinute(BaseModel):
    minute: str
    avg_latency_ms: float


class HeatmapSlot(BaseModel):
    minute: str
    label: str
    bins: List[int]


class UserSummary(BaseModel):
    total: int
    admin: int
    dev: int


class SimilarityQueryItem(BaseModel):
    ts: str
    query: str
    score: float
    doc_id: Optional[str] = None
    doc_title: Optional[str] = None


class InsightData(BaseModel):
    modules: List[ModuleState]
    modules_summary: ModulesSummary
    requests_per_minute: List[RequestPerMinute]
    latency_per_minute: List[LatencyPerMinute]
    similarity_heatmap: List[HeatmapSlot]
    user_summary: UserSummary
    top_similarity_queries: List[SimilarityQueryItem]
    demo_password_hint: str
    updated_at: str


@dataclass
class _RequestMinute:
    minute: datetime
    count: int = 0
    total_latency_ms: int = 0

    @property
    def avg_latency_ms(self) -> float:
        if self.count <= 0:
            return 0.0
        return self.total_latency_ms / self.count

    def to_request_payload(self) -> Dict[str, object]:
        return {"minute": self.minute.isoformat(), "count": self.count}

    def to_latency_payload(self) -> Dict[str, object]:
        avg = round(self.avg_latency_ms, 2)
        return {"minute": self.minute.isoformat(), "avg_latency_ms": avg}


@dataclass
class _HeatBucket:
    minute: datetime
    bins: List[int] = field(default_factory=lambda: [0] * 10)

    def add(self, score: float) -> None:
        bins_len = len(self.bins)
        idx = max(0, min(bins_len - 1, int(score * bins_len - 1e-9)))
        self.bins[idx] += 1

    def to_payload(self) -> Dict[str, object]:
        return {
            "minute": self.minute.isoformat(),
            "label": self.minute.strftime("%H:%M"),
            "bins": list(self.bins),
        }


@dataclass
class _SimilarityQuery:
    ts: datetime
    query: str
    score: float
    doc_id: Optional[str]
    doc_title: Optional[str]

    def to_payload(self) -> Dict[str, object]:
        return {
            "ts": self.ts.isoformat(),
            "query": self.query,
            "score": round(self.score, 4),
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
        }

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
    request_minutes: Deque[_RequestMinute]
    similarity_heatmap: Deque[_HeatBucket]
    similarity_queries: Deque[_SimilarityQuery]
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
    request_minutes=deque(maxlen=120),
    similarity_heatmap=deque(maxlen=48),
    similarity_queries=deque(maxlen=50),
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
        now_minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        if state.similarity_heatmap and state.similarity_heatmap[-1].minute == now_minute:
            bucket = state.similarity_heatmap[-1]
        else:
            bucket = _HeatBucket(minute=now_minute)
            state.similarity_heatmap.append(bucket)
        bucket.add(s)
        heatmap_payload = [hb.to_payload() for hb in list(state.similarity_heatmap)[-12:]]
        updated_at = _now_iso()
    try:
        await _broadcast({
            "type": "ai_similarity",
            "ai": await get_ai_stats_raw(),
            "similarity": s,
        })
        await _broadcast({
            "type": "insight",
            "insight": {
                "similarity_heatmap": heatmap_payload,
                "updated_at": updated_at,
            },
        })
    except Exception:
        pass


async def record_similarity_query(
    query: str,
    results: List[Tuple[float, str, str, Optional[Dict[str, Any]]]],
) -> None:
    query_text = query.strip()
    top_score = 0.0
    top_doc = None
    doc_title = None
    if results:
        best = results[0]
        top_score = float(best[0])
        top_doc = best[1]
        meta = best[3] or {}
        if isinstance(meta, dict) and meta.get("title"):
            doc_title = str(meta.get("title"))
        elif best[2]:
            doc_title = str(best[2])[:80]
    async with state.lock:
        record = _SimilarityQuery(
            ts=datetime.now(timezone.utc),
            query=query_text,
            score=max(0.0, min(1.0, top_score)),
            doc_id=top_doc,
            doc_title=doc_title,
        )
        state.similarity_queries.append(record)
        top_payload = [item.to_payload() for item in list(state.similarity_queries)[-10:]]
        updated_at = _now_iso()
    try:
        await _broadcast({
            "type": "insight",
            "insight": {
                "top_similarity_queries": top_payload,
                "updated_at": updated_at,
            },
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
    ttl = max(1, int(settings.METRICS_CACHE_TTL_SECONDS))
    now = asyncio.get_event_loop().time()
    if _ai_stats_cache["val"] is not None and (now - float(_ai_stats_cache.get("ts", 0.0))) <= ttl:
        return AIStats(**(_ai_stats_cache["val"]))  # type: ignore[arg-type]
    data = await get_ai_stats_raw()
    _ai_stats_cache.update({"ts": now, "val": data})
    return AIStats(**data)  # type: ignore[arg-type]


@router.get("/api/v1/ai/history")
async def get_ai_history(limit: int = 120) -> Dict[str, object]:
    ttl = max(1, int(settings.METRICS_CACHE_TTL_SECONDS))
    now = asyncio.get_event_loop().time()
    if (
        _ai_history_cache["val"] is not None
        and _ai_history_cache.get("args") == limit
        and (now - float(_ai_history_cache.get("ts", 0.0))) <= ttl
    ):
        return _ai_history_cache["val"]  # type: ignore[return-value]
    async with state.lock:
        lat = list(state.ai_latencies)[-max(1, min(limit, state.ai_latencies.maxlen or 120)) :]
        sims = list(state.ai_similarity_samples)[-max(1, min(limit, state.ai_similarity_samples.maxlen or 120)) :]
        out = {"latencies": [p.model_dump() for p in lat], "similarities": sims}
    _ai_history_cache.update({"ts": now, "args": limit, "val": out})
    return out


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
    ttl = max(1, int(settings.METRICS_CACHE_TTL_SECONDS))
    now = asyncio.get_event_loop().time()
    if (
        _metrics_history_cache["val"] is not None
        and _metrics_history_cache.get("args") == limit
        and (now - float(_metrics_history_cache.get("ts", 0.0))) <= ttl
    ):
        return _metrics_history_cache["val"]  # type: ignore[return-value]
    async with state.lock:
        data = list(state.points)[-max(1, min(limit, state.max_points)) :]
        out = {"points": [p.model_dump() for p in data]}
    _metrics_history_cache.update({"ts": now, "args": limit, "val": out})
    return out


@router.get("/api/v1/dashboard/insight", response_model=InsightData)
async def get_dashboard_insight() -> InsightData:
    ttl = max(1, int(settings.METRICS_CACHE_TTL_SECONDS))
    now = asyncio.get_event_loop().time()
    if _insight_cache["val"] is not None and (now - float(_insight_cache.get("ts", 0.0))) <= ttl:
        return InsightData(**(_insight_cache["val"]))  # type: ignore[arg-type]
    data = await get_insight_snapshot(include_modules=True)
    _insight_cache.update({"ts": now, "val": data})
    return InsightData(**data)  # type: ignore[arg-type]


async def record_request_metric(path: str, status_code: int, duration_ms: int) -> None:
    if path.startswith("/ws"):
        return
    duration_ms = max(0, int(duration_ms))
    _ = status_code  # placeholder for future status breakdowns
    minute_bucket = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    async with state.lock:
        if state.request_minutes and state.request_minutes[-1].minute == minute_bucket:
            bucket = state.request_minutes[-1]
        else:
            bucket = _RequestMinute(minute=minute_bucket)
            state.request_minutes.append(bucket)
        bucket.count += 1
        bucket.total_latency_ms += duration_ms
        requests_payload = [rm.to_request_payload() for rm in list(state.request_minutes)[-30:]]
        latency_payload = [rm.to_latency_payload() for rm in list(state.request_minutes)[-30:]]
        updated_at = _now_iso()
    try:
        await _broadcast({
            "type": "insight",
            "insight": {
                "requests_per_minute": requests_payload,
                "latency_per_minute": latency_payload,
                "updated_at": updated_at,
            },
        })
    except Exception:
        pass


async def get_insight_snapshot(include_modules: bool = True) -> Dict[str, object]:
    modules_payload: List[Dict[str, object]] = []
    modules_summary = {"total": 0, "enabled": 0, "disabled": 0}
    user_summary = get_user_statistics()
    password_hint = get_demo_password_hint()
    if include_modules:
        modules_map = modules_core.list_modules()
        modules_payload = [
            {"name": name, "enabled": bool(enabled)}
            for name, enabled in sorted(modules_map.items())
        ]
        enabled = sum(1 for _, flag in modules_map.items() if flag)
        modules_summary = {
            "total": len(modules_map),
            "enabled": enabled,
            "disabled": len(modules_map) - enabled,
        }
    async with state.lock:
        requests_series = list(state.request_minutes)[-30:]
        heatmap_series = list(state.similarity_heatmap)[-12:]
        queries_series = list(state.similarity_queries)[-10:]
    requests_payload = [rm.to_request_payload() for rm in requests_series]
    latency_payload = [rm.to_latency_payload() for rm in requests_series]
    heatmap_payload = [hb.to_payload() for hb in heatmap_series]
    top_queries_payload = [q.to_payload() for q in queries_series]
    payload: Dict[str, object] = {
        "requests_per_minute": requests_payload,
        "latency_per_minute": latency_payload,
        "similarity_heatmap": heatmap_payload,
        "user_summary": user_summary,
        "top_similarity_queries": top_queries_payload,
        "demo_password_hint": password_hint,
        "updated_at": _now_iso(),
    }
    if include_modules:
        payload["modules"] = modules_payload
        payload["modules_summary"] = modules_summary
    return payload


@router.websocket("/ws/metrics")
async def ws_metrics(ws: WebSocket):
    await ws.accept()
    state.clients.append(ws)
    try:
        # send snapshot on connect
        async with state.lock:
            points = [p.model_dump() for p in state.points]
            tasks = state.tasks.model_dump()
            reports = state.reports.model_dump()
        ai_stats = await get_ai_stats_raw()
        insight = await get_insight_snapshot(include_modules=True)
        snapshot = {
            "type": "snapshot",
            "points": points,
            "tasks": tasks,
            "reports": reports,
            "ai": ai_stats,
            "insight": insight,
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

