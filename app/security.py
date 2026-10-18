"""security.py - two shared secrets, both fail CLOSED (no secret configured = refuse).

- X-API-Key           protects the lead/appointment endpoints you or your client's
                      form/CRM call. Value: LEADS_API_KEY.
- Webhook secret      protects /vapi/webhook. Vapi sends it back on every request
                      when you attach a custom credential to the tools/assistant:
                      either `Authorization: Bearer <secret>` or an
                      `X-Vapi-Secret: <secret>` header (both are accepted).
                      Value: WEBHOOK_SECRET.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from app.config import get_settings


def _same(candidate: str, expected: str) -> bool:
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


async def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    expected = get_settings().api_key
    if not expected:
        raise HTTPException(status_code=503, detail="LEADS_API_KEY is not configured on the server")
    if not x_api_key or not _same(x_api_key, expected):
        raise HTTPException(status_code=401, detail="invalid API key")


async def require_webhook_secret(request: Request) -> None:
    expected = get_settings().webhook_secret
    if not expected:
        raise HTTPException(status_code=503, detail="WEBHOOK_SECRET is not configured on the server")

    candidates: list[str] = []
    secret_header = request.headers.get("x-vapi-secret")
    if secret_header:
        candidates.append(secret_header)
    auth = request.headers.get("authorization")
    if auth:
        candidates.append(auth[7:] if auth.lower().startswith("bearer ") else auth)

    if not any(_same(value, expected) for value in candidates):
        raise HTTPException(status_code=401, detail="invalid webhook secret")
