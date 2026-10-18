"""vapi_client.py - start an outbound phone call through Vapi.

Verified request shape (Vapi docs): POST {base}/call with
`Authorization: Bearer <API key>` and a JSON body of assistantId,
phoneNumberId, customer{number, name}, metadata, and
assistantOverrides.variableValues (fills the {{name}} etc. in the prompt).
The response contains the new call's `id` and status "queued".
"""

from __future__ import annotations

import httpx

from app.config import get_settings


class VapiError(Exception):
    """The call could not be started. Message is safe to store and show to the operator."""


async def start_outbound_call(
    *, name: str, phone: str, lead_id: int, variables: dict[str, str]
) -> str:
    settings = get_settings()
    missing = [
        label
        for label, value in (
            ("VAPI_API_KEY", settings.vapi_api_key),
            ("VAPI_ASSISTANT_ID", settings.vapi_assistant_id),
            ("VAPI_PHONE_NUMBER_ID", settings.vapi_phone_number_id),
        )
        if not value
    ]
    if missing:
        raise VapiError(f"missing configuration: {', '.join(missing)}")

    body = {
        "assistantId": settings.vapi_assistant_id,
        "phoneNumberId": settings.vapi_phone_number_id,
        "customer": {"number": phone, "name": name},
        "metadata": {"lead_id": lead_id},
        "assistantOverrides": {"variableValues": variables},
    }
    headers = {"Authorization": f"Bearer {settings.vapi_api_key}"}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(f"{settings.vapi_base_url}/call", json=body, headers=headers)
    except httpx.HTTPError as exc:
        raise VapiError(f"could not reach Vapi: {exc.__class__.__name__}") from exc

    if response.status_code >= 400:
        raise VapiError(f"Vapi returned {response.status_code}: {response.text[:300]}")
    try:
        call_id = response.json().get("id")
    except ValueError:
        call_id = None
    if not call_id:
        raise VapiError("Vapi response did not include a call id")
    return str(call_id)
