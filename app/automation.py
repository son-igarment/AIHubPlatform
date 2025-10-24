import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import settings


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
        if not settings.AI_CRAWL_ENDPOINT:
            logger.info("AI_CRAWL_ENDPOINT not configured; crawl step skipped.")
            return {"status": "skipped", "reason": "AI_CRAWL_ENDPOINT not set"}

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
        if not settings.AI_UPDATE_ENDPOINT:
            logger.info("AI_UPDATE_ENDPOINT not configured; update step skipped.")
            return {"status": "skipped", "reason": "AI_UPDATE_ENDPOINT not set"}

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
        return response

    async def _notify_telegram(self, result: AutomationResult) -> None:
        if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
            logger.debug("Telegram credentials missing; notification skipped.")
            return

        message = result.to_message()

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


automation_scheduler = AutomationScheduler()

