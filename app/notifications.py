import asyncio
import logging
from typing import Any, Dict, Optional

import requests

from .config import settings
from .resilience import resilient_request, CircuitOpenError


logger = logging.getLogger("modules")


async def send_telegram_message(text: str) -> None:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        logger.debug("Telegram credentials missing; notification skipped.")
        return

    def _send() -> None:
        url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload: Dict[str, Any] = {
            "chat_id": settings.TELEGRAM_CHAT_ID,
            "text": text,
            "disable_notification": settings.TELEGRAM_DISABLE_NOTIFICATIONS,
            "parse_mode": settings.TELEGRAM_PARSE_MODE,
        }
        if settings.TELEGRAM_THREAD_ID:
            payload["message_thread_id"] = settings.TELEGRAM_THREAD_ID
        try:
            resp = resilient_request(
                "POST",
                url,
                timeout=settings.TELEGRAM_TIMEOUT,
                retries=settings.HTTP_MAX_RETRIES,
                backoff_base_ms=settings.HTTP_BACKOFF_BASE_MS,
                circuit_key="telegram:sendMessage",
                circuit_fail_threshold=settings.HTTP_CIRCUIT_FAIL_THRESHOLD,
                circuit_reset_sec=settings.HTTP_CIRCUIT_RESET_SEC,
                json=payload,
            )
            resp.raise_for_status()
        except CircuitOpenError:
            # Drop silently to avoid blocking app under outages
            logger.warning("Telegram circuit open; skipping notification")

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:  # pylint: disable=broad-except
        logging.getLogger(__name__).exception("Failed to send Telegram message: %s", exc)
