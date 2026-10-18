"""vapi_webhook.py - the endpoint Vapi calls while a call is running and after it ends.

One URL, two jobs (verified against Vapi's server-events docs):
  * "tool-calls"          -> the agent wants an action (check times, book, save notes).
                             We answer {"results": [{"toolCallId", "result"|"error"}]}.
  * "end-of-call-report"  -> the call is over; we store transcript/summary and set
                             the lead's final status.
Anything else (status updates, speech events...) is acknowledged and ignored.
Vapi can redeliver messages, so every handler is safe to run twice.
"""

from __future__ import annotations

import json
import logging
import re

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import Appointment, CallLog, Lead, get_db
from app.notify import notify
from app.phone import normalize_phone
from app.scheduling import book_slot, free_slots
from app.security import require_webhook_secret
from app.slots import SlotError, format_slot, human_slot, parse_local
from app.vapi_parse import ToolCall, classify_outcome, parse_report, parse_tool_calls

logger = logging.getLogger(__name__)
router = APIRouter(tags=["vapi"], dependencies=[Depends(require_webhook_secret)])

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def _find_lead(db: AsyncSession, message: dict) -> Lead | None:
    """Match a call to its lead: by Vapi call id first, then by the dialled number."""
    call = message.get("call") or {}
    call_id = call.get("id")
    if call_id:
        lead = (
            await db.execute(select(Lead).where(Lead.vapi_call_id == str(call_id)))
        ).scalar_one_or_none()
        if lead is not None:
            return lead
    number = (call.get("customer") or {}).get("number")
    if number:
        try:
            phone = normalize_phone(str(number))
        except ValueError:
            return None
        return (
            await db.execute(
                select(Lead).where(Lead.phone == phone).order_by(Lead.id.desc()).limit(1)
            )
        ).scalars().first()
    return None


def _slot_lines(slots: list) -> str:
    return "; ".join(f"{format_slot(s)} ({human_slot(s)})" for s in slots)


async def _check_availability(db: AsyncSession, args: dict) -> str:
    settings = get_settings()
    schedule = settings.schedule()
    from_day = None
    raw_date = str(args.get("date") or "").strip()
    if raw_date:
        if not _DATE.match(raw_date):
            return "error: date must look like YYYY-MM-DD"
        try:
            from_day = parse_local(schedule, raw_date + " 00:00").date()
        except SlotError:
            return "error: that is not a real calendar date"
    slots = await free_slots(db, schedule, from_day=from_day, limit=6)
    if not slots:
        return (
            f"No free times in the next {settings.booking_horizon_days} days. "
            "Tell the caller the team will call them back to arrange a time."
        )
    return f"Free times (timezone {settings.business_tz}): {_slot_lines(slots)}"


async def _book_appointment(
    db: AsyncSession, args: dict, lead: Lead | None, background: BackgroundTasks
) -> str:
    settings = get_settings()
    schedule = settings.schedule()
    name = str(args.get("name") or (lead.name if lead else "")).strip()
    phone_raw = str(args.get("phone") or (lead.phone if lead else "")).strip()
    if not name or not phone_raw:
        return "error: need the caller's name and phone number to book"
    try:
        phone = normalize_phone(phone_raw, settings.default_country_code)
    except ValueError as exc:
        return f"error: {exc}"

    try:
        appointment = await book_slot(
            db,
            schedule,
            start_text=str(args.get("start_time") or ""),
            name=name,
            phone=phone,
            lead_id=lead.id if lead else None,
            notes=(str(args["notes"])[:500] if args.get("notes") else None),
        )
    except SlotError as exc:
        alternatives = await free_slots(db, schedule, limit=3)
        hint = f" Nearest free times: {_slot_lines(alternatives)}" if alternatives else ""
        return f"error: {exc}{hint}"

    start_local = parse_local(schedule, str(args.get("start_time")))
    when = f"{human_slot(start_local)} ({settings.business_tz})"
    if lead is not None:
        lead.status = "booked"
        await db.commit()
    background.add_task(
        notify,
        "appointment_booked",
        f"New booking: {name} ({phone}) on {when}",
        {"appointment_id": appointment.id, "lead_id": appointment.lead_id},
    )
    return f"Booked for {when}. Confirm the day and time to the caller."


async def _save_qualification(
    db: AsyncSession, args: dict, lead: Lead | None, background: BackgroundTasks
) -> str:
    level = str(args.get("interest_level") or "").strip().lower()
    if level not in {"hot", "warm", "cold", "not_interested"}:
        return "error: interest_level must be hot, warm, cold or not_interested"
    if lead is None:
        return "Noted (no matching lead on record)."

    details = {
        key: str(args[key])[:500]
        for key in ("service_interest", "budget", "timeline", "notes")
        if args.get(key)
    }
    lead.interest_level = level
    lead.qualification = json.dumps(details) if details else None
    if lead.status != "booked":
        lead.status = {
            "hot": "qualified",
            "warm": "qualified",
            "cold": "not_interested",
            "not_interested": "not_interested",
        }[level]
    await db.commit()
    if level == "hot":
        background.add_task(
            notify,
            "hot_lead",
            f"Hot lead: {lead.name} ({lead.phone})",
            {"lead_id": lead.id, **details},
        )
    return "Saved."


async def _run_tool(
    call: ToolCall, db: AsyncSession, lead: Lead | None, background: BackgroundTasks
) -> dict:
    try:
        if call.name == "check_availability":
            text = await _check_availability(db, call.args)
        elif call.name == "book_appointment":
            text = await _book_appointment(db, call.args, lead, background)
        elif call.name == "save_qualification":
            text = await _save_qualification(db, call.args, lead, background)
        else:
            return {"name": call.name, "toolCallId": call.id, "error": f"unknown tool {call.name!r}"}
    except Exception:  # noqa: BLE001 - never let a tool bug break a live call
        logger.exception("tool %s failed", call.name)
        await db.rollback()
        return {
            "name": call.name,
            "toolCallId": call.id,
            "error": "something went wrong; tell the caller the team will follow up",
        }
    if text.startswith("error:"):
        return {"name": call.name, "toolCallId": call.id, "error": text[len("error:"):].strip()}
    return {"name": call.name, "toolCallId": call.id, "result": text}


async def _handle_report(db: AsyncSession, message: dict) -> None:
    report = parse_report(message)
    if report is None:
        return
    lead = await _find_lead(db, message)

    log = (
        await db.execute(select(CallLog).where(CallLog.vapi_call_id == report.call_id))
    ).scalar_one_or_none()
    if log is None:
        log = CallLog(vapi_call_id=report.call_id)
        db.add(log)
    log.lead_id = lead.id if lead else None
    log.ended_reason = report.ended_reason[:120]
    log.summary = report.summary
    log.transcript = report.transcript
    log.duration_seconds = report.duration_seconds
    log.recording_url = report.recording_url

    if lead is not None:
        has_appointment = (
            await db.execute(select(Appointment.id).where(Appointment.lead_id == lead.id).limit(1))
        ).first() is not None
        lead.status = classify_outcome(report.ended_reason, has_appointment, lead.interest_level)
        if report.summary:
            lead.summary = report.summary
    await db.commit()


@router.post("/vapi/webhook")
async def vapi_webhook(
    request: Request, background: BackgroundTasks, db: AsyncSession = Depends(get_db)
) -> dict[str, object]:
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="body must be JSON") from exc
    message = body.get("message") if isinstance(body, dict) else None
    if not isinstance(message, dict):
        return {"ok": True}

    kind = message.get("type")
    if kind == "tool-calls":
        lead = await _find_lead(db, message)
        results = [
            await _run_tool(call, db, lead, background) for call in parse_tool_calls(message)
        ]
        return {"results": results}
    if kind == "end-of-call-report":
        await _handle_report(db, message)
    return {"ok": True}
