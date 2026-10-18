"""notify.py - optional alert when something important happens (a booking, a hot lead).

Set NOTIFY_WEBHOOK_URL to a Slack incoming webhook, Zapier, Make or n8n URL.
We POST {"text": ..., "event": ..., "data": {...}}. Slack reads "text"; the
others can use any field. Failures are logged and swallowed so an alert problem
never breaks a call.
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


async def notify(event: str, text: str, data: dict | None = None) -> None:
    url = get_settings().notify_webhook_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            await client.post(url, json={"text": text, "event": event, "data": data or {}})
    except Exception:  # noqa: BLE001 - alerts are best-effort
        logger.warning("notification for event %r failed", event)
