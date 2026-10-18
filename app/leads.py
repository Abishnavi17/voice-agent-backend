"""leads.py - lead intake and the outbound call that follows within seconds.

Flow: your client's ad form / CRM / Zapier POSTs a lead here -> we store it ->
if we are inside the allowed calling hours, Vapi phones the lead immediately.
Outside those hours the lead waits (status "pending_window") and a background
loop calls it as soon as the window opens.

Consent: the sender must set "consent": true, meaning this person agreed to be
contacted (e.g. ticked a box on the form). Without it we refuse and do not call.
You are responsible for following local calling rules (India DND/TRAI, US TCPA,
etc.); the call window and consent flag are there to help, not to replace that.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import Appointment, CallLog, Lead, SessionLocal, get_db
from app.phone import normalize_phone
from app.security import require_api_key
from app.slots import UTC, in_window
from app.vapi_client import VapiError, start_outbound_call

logger = logging.getLogger(__name__)
router = APIRouter(tags=["leads"], dependencies=[Depends(require_api_key)])

_CONTEXT_MAX_CHARS = 300  # keep untrusted form text short before it reaches the prompt


class LeadIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=5, max_length=32)
    email: str | None = Field(default=None, max_length=255)
    source: str | None = Field(default=None, max_length=80)
    message: str | None = Field(default=None, max_length=1000)
    consent: bool = False


def lead_to_dict(lead: Lead) -> dict[str, object]:
    return {
        "id": lead.id,
        "name": lead.name,
        "phone": lead.phone,
        "email": lead.email,
        "source": lead.source,
        "message": lead.message,
        "status": lead.status,
        "call_attempts": lead.call_attempts,
        "vapi_call_id": lead.vapi_call_id,
        "interest_level": lead.interest_level,
        "qualification": json.loads(lead.qualification) if lead.qualification else None,
        "summary": lead.summary,
        "created_at": lead.created_at.isoformat() if lead.created_at else None,
        "updated_at": lead.updated_at.isoformat() if lead.updated_at else None,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def calls_allowed_now(now: datetime | None = None) -> bool:
    settings = get_settings()
    open_at, close_at = settings.call_window_times()
    return in_window(open_at, close_at, now or _now(), settings.business_tz)


def build_context(lead: Lead) -> str:
    parts: list[str] = []
    if lead.source:
        parts.append(f"Came from: {lead.source[:80]}.")
    if lead.message:
        parts.append(f"They wrote: {lead.message[:_CONTEXT_MAX_CHARS]}")
    return " ".join(parts) or "No extra details were provided."


async def place_call(db: AsyncSession, lead: Lead) -> None:
    """Start the outbound call for a lead. Raises VapiError (lead is marked failed)."""
    settings = get_settings()
    lead.status = "calling"
    lead.call_attempts = (lead.call_attempts or 0) + 1
    await db.commit()
    try:
        call_id = await start_outbound_call(
            name=lead.name,
            phone=lead.phone,
            lead_id=lead.id,
            variables={
                "name": lead.name,
                "business_name": settings.business_name,
                "lead_context": build_context(lead),
                "timezone": settings.business_tz,
            },
        )
    except VapiError as exc:
        lead.status = "failed"
        lead.summary = f"Call could not be started: {exc}"[:500]
        await db.commit()
        raise
    lead.vapi_call_id = call_id
    await db.commit()


@router.post("/leads", status_code=202)
async def create_lead(payload: LeadIn, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    if not payload.consent:
        raise HTTPException(
            status_code=400,
            detail='consent must be true: only submit people who agreed to be contacted',
        )
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="name must not be blank")
    try:
        phone = normalize_phone(payload.phone, settings.default_country_code)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Same number twice within a few minutes = a double submit or a retry. Do not call twice.
    cutoff = _now() - timedelta(minutes=settings.dedupe_minutes)
    duplicate = (
        await db.execute(
            select(Lead)
            .where(Lead.phone == phone, Lead.created_at >= cutoff, Lead.status != "failed")
            .order_by(Lead.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        return {"lead_id": duplicate.id, "status": duplicate.status, "duplicate": True}

    lead = Lead(
        name=payload.name.strip(),
        phone=phone,
        email=payload.email,
        source=payload.source,
        message=payload.message,
        status="new",
        call_attempts=0,
    )
    db.add(lead)
    await db.commit()
    await db.refresh(lead)

    if not calls_allowed_now():
        lead.status = "pending_window"
        await db.commit()
        return {"lead_id": lead.id, "status": lead.status, "duplicate": False}

    try:
        await place_call(db, lead)
    except VapiError as exc:
        raise HTTPException(
            status_code=502, detail=f"lead {lead.id} saved but the call failed to start: {exc}"
        ) from exc
    return {"lead_id": lead.id, "status": lead.status, "duplicate": False}


@router.get("/leads")
async def list_leads(
    status: str | None = None, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    stmt = select(Lead).order_by(Lead.id.desc()).limit(100)
    if status:
        stmt = stmt.where(Lead.status == status)
    leads = (await db.execute(stmt)).scalars().all()
    return {"leads": [lead_to_dict(lead) for lead in leads]}


@router.get("/leads/{lead_id}")
async def get_lead(lead_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    appointments = (
        await db.execute(select(Appointment).where(Appointment.lead_id == lead_id))
    ).scalars().all()
    call_logs = (
        await db.execute(select(CallLog).where(CallLog.lead_id == lead_id).order_by(CallLog.id))
    ).scalars().all()
    return {
        **lead_to_dict(lead),
        "appointments": [
            {
                "id": a.id,
                "start_time_utc": a.start_time.isoformat(),
                "duration_minutes": a.duration_minutes,
                "notes": a.notes,
            }
            for a in appointments
        ],
        "call_logs": [
            {
                "vapi_call_id": c.vapi_call_id,
                "ended_reason": c.ended_reason,
                "summary": c.summary,
                "transcript": c.transcript,
                "duration_seconds": c.duration_seconds,
                "recording_url": c.recording_url,
            }
            for c in call_logs
        ],
    }


@router.post("/leads/{lead_id}/call", status_code=202)
async def recall_lead(lead_id: int, db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    """Manually (re)start the call for one lead, e.g. after a no-answer."""
    lead = await db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status_code=404, detail="lead not found")
    if lead.status == "calling":
        raise HTTPException(status_code=409, detail="a call for this lead is already in progress")
    if not calls_allowed_now():
        raise HTTPException(status_code=409, detail="outside the allowed calling hours")
    try:
        await place_call(db, lead)
    except VapiError as exc:
        raise HTTPException(status_code=502, detail=f"call failed to start: {exc}") from exc
    return {"lead_id": lead.id, "status": lead.status}


@router.get("/appointments")
async def list_appointments(db: AsyncSession = Depends(get_db)) -> dict[str, object]:
    rows = (
        await db.execute(
            select(Appointment).where(Appointment.start_time >= _now()).order_by(Appointment.start_time)
        )
    ).scalars().all()
    return {
        "appointments": [
            {
                "id": a.id,
                "lead_id": a.lead_id,
                "name": a.name,
                "phone": a.phone,
                "start_time_utc": a.start_time.isoformat(),
                "duration_minutes": a.duration_minutes,
                "notes": a.notes,
            }
            for a in rows
        ]
    }


async def process_pending() -> int:
    """Call every lead that was waiting for the calling window. Returns how many were started."""
    if not calls_allowed_now():
        return 0
    started = 0
    async with SessionLocal() as db:
        waiting = (
            await db.execute(
                select(Lead).where(Lead.status == "pending_window").order_by(Lead.id).limit(20)
            )
        ).scalars().all()
        for lead in waiting:
            try:
                await place_call(db, lead)
                started += 1
            except VapiError:
                logger.warning("pending lead %s could not be called", lead.id)
    return started


async def pending_loop(interval_seconds: int = 60) -> None:
    """Background task: every minute, call leads that were waiting for the window."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await process_pending()
        except Exception:  # noqa: BLE001 - keep the loop alive
            logger.exception("pending-lead loop iteration failed")
