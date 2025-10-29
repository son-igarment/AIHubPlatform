import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, List, Set

import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings
from . import embeddings as emb


logger = logging.getLogger("automation")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_payload(raw_payload: Optional[str]) -> Dict[str, Any]:
    if not raw_payload:
        return {}
    try:
        data = json.loads(raw_payload)
        if isinstance(data, dict):
            return data
        logger.warning("Payload must be a JSON object; received %s", type(data).__name__)
        return {}
    except json.JSONDecodeError:
        logger.warning("Failed to decode payload JSON. Raw input will be ignored.")
        return {}


def _summarize_dict(data: Dict[str, Any], limit: int = 400) -> str:
    try:
        payload = json.dumps(data, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = str(data)
    if len(payload) <= limit:
        return payload
    return payload[: limit - 3] + "..."


@dataclass
class AutomationResult:
    status: str
    started_at: datetime
    finished_at: datetime
    origin: str
    crawl_summary: Dict[str, Any] = field(default_factory=dict)
    update_summary: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def to_message(self) -> str:
        emoji = "✅" if self.status == "success" else "⚠️"
        lines = [
            f"{emoji} AI data automation ({self.origin})",
            f"Status: {self.status}",
            f"Started: {self.started_at.isoformat()}",
            f"Finished: {self.finished_at.isoformat()}",
            f"Duration: {self.duration_seconds:.1f}s",
        ]
        if self.crawl_summary:
            lines.append(f"Crawl: {_summarize_dict(self.crawl_summary)}")
        if self.update_summary:
            lines.append(f"Update: {_summarize_dict(self.update_summary)}")
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


class AutomationScheduler:
    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone=timezone.utc)
        self._job_id = "ai_data_refresh"
        self._lock = asyncio.Lock()
        self._last_result: Optional[AutomationResult] = None
        # v3 state
        self._seen_doc_ids: Set[str] = set()
        self._sim_counter: int = 0

    async def start(self) -> None:
        if not settings.AUTOMATION_ENABLED:
            logger.info("Automation scheduler disabled via configuration.")
            return

        if not self._scheduler.running:
            self._scheduler.start()
            logger.info(
                "Automation scheduler started (interval=%sh, run_at_startup=%s).",
                settings.AUTOMATION_INTERVAL_HOURS,
                settings.AUTOMATION_RUN_AT_STARTUP,
            )

        if not self._scheduler.get_job(self._job_id):
            trigger = IntervalTrigger(
                hours=settings.AUTOMATION_INTERVAL_HOURS,
                timezone=timezone.utc,
            )
            self._scheduler.add_job(
                self._schedule_wrapper,
                trigger=trigger,
                id=self._job_id,
                max_instances=1,
                replace_existing=True,
                name="AI data refresh",
            )
            logger.info("Scheduled automation job every %s hours.", settings.AUTOMATION_INTERVAL_HOURS)

        if settings.AUTOMATION_RUN_AT_STARTUP:
            await self.run_now(origin="startup")

        # v3: Optional quick simulation cycles (>=2) to validate behavior
        if settings.AUTOMATION_SIMULATE_CYCLES >= 2:
            await self._run_simulation(settings.AUTOMATION_SIMULATE_CYCLES)

    async def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Automation scheduler shut down.")

    async def run_now(self, origin: str = "manual") -> Optional[AutomationResult]:
        if not settings.AUTOMATION_ENABLED:
            logger.info("Automation run skipped; scheduler disabled.")
            return None

        if self._lock.locked():
            logger.warning("Automation job already running; skipping new invocation (%s).", origin)
            return None

        async with self._lock:
            started = _utcnow()
            crawl_summary: Dict[str, Any] = {}
            update_summary: Dict[str, Any] = {}
            error: Optional[str] = None
            status = "success"

            logger.info("Automation job started (%s).", origin)
            try:
                crawl_summary = await self._crawl_data()
                update_summary = await self._update_data(crawl_summary)
            except Exception as exc:  # pylint: disable=broad-except
                status = "failure"
                error = str(exc)
                logger.exception("Automation job failed: %s", exc)

            finished = _utcnow()
            result = AutomationResult(
                status=status,
                started_at=started,
                finished_at=finished,
                origin=origin,
                crawl_summary=crawl_summary,
                update_summary=update_summary,
                error=error,
            )
            self._last_result = result
            await self._notify_telegram(result)
            # Emit a valid JSON line with job summary (v3)
            try:
                logger.info(
                    json.dumps(
                        {
                            "version": "scheduler_v3",
                            "status": status,
                            "origin": origin,
                            "started_at": started.isoformat(),
                            "finished_at": finished.isoformat(),
                            "duration_seconds": result.duration_seconds,
                            "crawl": crawl_summary,
                            "update": update_summary,
                        },
                        ensure_ascii=False,
                        default=str,
                        sort_keys=True,
                    )
                )
            except Exception:
                logger.exception("Failed to emit JSON job log")
            logger.info(
                "Automation job finished (%s) status=%s duration=%.2fs",
                origin,
                status,
                result.duration_seconds,
            )
            return result

    async def _schedule_wrapper(self) -> None:
        await self.run_now(origin="scheduler")

    async def _crawl_data(self) -> Dict[str, Any]:
        """v3: Crawl keywords; simulate when endpoint missing.

        Preferred shape: {"items": [{"doc_id": str, "content": str, "meta": {...}}, ...]}
        """
        if not settings.AI_CRAWL_ENDPOINT:
            self._sim_counter += 1
            cycle = self._sim_counter
            items: List[Dict[str, Any]] = [
                {"doc_id": "kw:fastapi", "content": "FastAPI tutorial and tips", "meta": {"lang": "en"}},
                {"doc_id": "kw:apscheduler", "content": "APScheduler interval job guide", "meta": {"lang": "en"}},
                {"doc_id": "kw:embeddings", "content": "Local vs OpenAI embeddings", "meta": {"lang": "en"}},
            ]
            if cycle >= 2:
                items.append({"doc_id": "kw:fastapi", "content": "FastAPI tutorial and tips", "meta": {"cycle": cycle}})  # duplicate
                items.append({"doc_id": "kw:embeddings", "content": "OpenAI embeddings vs local BM25", "meta": {"cycle": cycle}})  # updated
                items.append({"doc_id": f"kw:new-{cycle}", "content": f"New item cycle {cycle}", "meta": {"cycle": cycle}})
            logger.info("Simulated crawl cycle=%s items=%s", cycle, len(items))
            return {"status": "ok", "source": "simulated", "cycle": cycle, "items": items}

        payload = _parse_payload(settings.AI_CRAWL_PAYLOAD)
        logger.info("Crawling AI data from %s", settings.AI_CRAWL_ENDPOINT)
        response = await asyncio.to_thread(
            self._perform_request,
            settings.AI_CRAWL_ENDPOINT,
            settings.AI_CRAWL_METHOD,
            payload,
        )
        logger.info("Crawl request status=%s", response.get("status_code"))
        return response

    async def _update_data(self, crawl_summary: Dict[str, Any]) -> Dict[str, Any]:
        """v3: Update embeddings with dedupe and rows_changed accounting.

        If external AI_UPDATE_ENDPOINT is set, delegate and normalize response; else perform local upserts.
        """
        if settings.AI_UPDATE_ENDPOINT:
            payload = _parse_payload(settings.AI_UPDATE_PAYLOAD)
            payload.setdefault("data", crawl_summary.get("data") or crawl_summary)
            logger.info("Updating AI data via %s", settings.AI_UPDATE_ENDPOINT)
            response = await asyncio.to_thread(
                self._perform_request,
                settings.AI_UPDATE_ENDPOINT,
                settings.AI_UPDATE_METHOD,
                payload,
            )
            logger.info("Update request status=%s", response.get("status_code"))
            rows_changed = 0
            processed = 0
            try:
                data = response.get("data", {})
                if isinstance(data, dict):
                    rc = data.get("rows_changed")
                    pr = data.get("processed")
                    if isinstance(rc, int):
                        rows_changed = rc
                    if isinstance(pr, int):
                        processed = pr
            except Exception:
                pass
            return {"status": "ok", "mode": "external", "rows_changed": rows_changed, "processed": processed, "raw": response}

        # Local bulk upsert path
        items: List[Dict[str, Any]] = []
        if isinstance(crawl_summary.get("items"), list):
            items = [x for x in crawl_summary["items"] if isinstance(x, dict)]
        elif isinstance(crawl_summary.get("data"), list):
            items = [x for x in crawl_summary["data"] if isinstance(x, dict)]
        elif isinstance(crawl_summary.get("data"), dict) and isinstance(crawl_summary["data"].get("items"), list):
            items = [x for x in crawl_summary["data"]["items"] if isinstance(x, dict)]

        processed = 0
        inserted = 0
        updated = 0
        duplicates_skipped = 0

        con = emb._connect()
        try:
            for raw in items:
                doc_id = str(raw.get("doc_id") or raw.get("id") or "").strip()
                content = raw.get("content")
                meta = raw.get("meta") if isinstance(raw.get("meta"), dict) else None
                if not doc_id or not isinstance(content, str) or not content.strip():
                    continue

                processed += 1
                # in-run duplicate guard
                if doc_id in self._seen_doc_ids:
                    duplicates_skipped += 1
                    continue
                self._seen_doc_ids.add(doc_id)

                cur = con.execute("SELECT content FROM documents WHERE doc_id=?", (doc_id,))
                row = cur.fetchone()
                if row is None:
                    emb_vec = emb.compute_embedding(content)
                    emb._upsert_document(doc_id, content, emb_vec, meta)
                    inserted += 1
                    continue

                prev_content = row[0] if isinstance(row[0], str) else None
                if prev_content == content:
                    duplicates_skipped += 1
                    continue

                emb_vec = emb.compute_embedding(content)
                emb._upsert_document(doc_id, content, emb_vec, meta)
                updated += 1
        finally:
            con.close()

        rows_changed = inserted + updated
        summary = {
            "status": "ok",
            "mode": "local",
            "processed": processed,
            "inserted": inserted,
            "updated": updated,
            "duplicates_skipped": duplicates_skipped,
            "rows_changed": rows_changed,
        }
        logger.info("v3 update summary: %s", _summarize_dict(summary))
        return summary

    async def _notify_telegram(self, result: AutomationResult) -> None:
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            logger.debug("Telegram credentials missing; notification skipped.")
            return

        # Add record counts to Telegram message if available
        rows_changed = None
        processed = None
        if isinstance(result.update_summary, dict):
            rows_changed = result.update_summary.get("rows_changed")
            processed = result.update_summary.get("processed")
        extra = ""
        if isinstance(rows_changed, int) and isinstance(processed, int):
            extra = f"\nRecords: rows_changed={rows_changed} processed={processed}"
        message = result.to_message() + extra

        def _send() -> None:
            url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
            payload: Dict[str, Any] = {
                "chat_id": settings.TELEGRAM_CHAT_ID,
                "text": message,
                "disable_notification": settings.TELEGRAM_DISABLE_NOTIFICATIONS,
                "parse_mode": settings.TELEGRAM_PARSE_MODE,
            }
            if settings.TELEGRAM_THREAD_ID:
                payload["message_thread_id"] = settings.TELEGRAM_THREAD_ID

            resp = requests.post(url, json=payload, timeout=settings.TELEGRAM_TIMEOUT)
            resp.raise_for_status()

        try:
            await asyncio.to_thread(_send)
            logger.info("Telegram notification sent.")
        except Exception as exc:  # pylint: disable=broad-except
            logger.exception("Failed to send Telegram notification: %s", exc)

    def _perform_request(self, url: str, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        method_upper = method.upper()
        headers = self._build_request_headers()
        kwargs: Dict[str, Any] = {"timeout": settings.AI_HTTP_TIMEOUT, "headers": headers}
        if method_upper in {"GET", "DELETE"}:
            kwargs["params"] = payload
        else:
            kwargs["json"] = payload

        resp = requests.request(method_upper, url, **kwargs)
        resp.raise_for_status()

        try:
            data = resp.json()
        except ValueError:
            data = {"raw": resp.text}

        return {
            "endpoint": url,
            "method": method_upper,
            "status_code": resp.status_code,
            "data": data,
        }

    def _build_request_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "AIHubAutomation/1.0"}
        if settings.AI_API_KEY:
            headers["Authorization"] = f"Bearer {settings.AI_API_KEY}"
        if settings.AI_EXTRA_HEADERS:
            try:
                extra = json.loads(settings.AI_EXTRA_HEADERS)
                if isinstance(extra, dict):
                    headers.update({str(k): str(v) for k, v in extra.items()})
            except json.JSONDecodeError:
                logger.warning("Invalid AI_EXTRA_HEADERS; expected JSON object string.")
        return headers

    @property
    def last_result(self) -> Optional[AutomationResult]:
        return self._last_result

    async def _run_simulation(self, cycles: int) -> None:
        for i in range(max(0, cycles)):
            await self.run_now(origin=f"simulation#{i+1}")
            await asyncio.sleep(max(0.0, settings.AUTOMATION_SIMULATE_DELAY_SEC))


automation_scheduler = AutomationScheduler()

