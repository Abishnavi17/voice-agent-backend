"""cache.py - Redis cache behind get_cached_reply / set_cached_reply (unchanged)."""

from __future__ import annotations

import hashlib
import os

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
_TTL_SECONDS = 3600
_client = redis.from_url(REDIS_URL, decode_responses=True)


def make_key(context: str) -> str:
    digest = hashlib.sha256(context.encode("utf-8")).hexdigest()
    return f"reply:{digest}"


async def get_cached_reply(context: str) -> str | None:
    try:
        return await _client.get(make_key(context))
    except Exception:  # noqa: BLE001
        return None


async def set_cached_reply(context: str, reply: str) -> None:
    try:
        await _client.set(make_key(context), reply, ex=_TTL_SECONDS)
    except Exception:  # noqa: BLE001
        pass
