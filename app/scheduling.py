"""scheduling.py - which slots are free, and booking one safely.

The slot math is in slots.py. This file adds the database part: subtracting
existing appointments, and inserting a new one. Double-booking is prevented
twice: we check first for a friendly error, and the UNIQUE constraint on
Appointment.start_time catches a race between two simultaneous calls.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import Appointment
from app.slots import (
    UTC,
    Schedule,
    SlotError,
    as_utc,
    candidate_slots,
    is_bookable_start,
    parse_local,
)


async def free_slots(
    db: AsyncSession,
    schedule: Schedule,
    *,
    from_day: date | None = None,
    limit: int = 6,
    now: datetime | None = None,
) -> list[datetime]:
    """Next free slot starts (local-time aware), earliest first."""
    now = now or datetime.now(UTC)
    candidates = candidate_slots(schedule, now, from_day)
    if not candidates:
        return []
    low, high = as_utc(candidates[0]), as_utc(candidates[-1])
    rows = (
        await db.execute(
            select(Appointment.start_time).where(
                Appointment.start_time >= low, Appointment.start_time <= high
            )
        )
    ).scalars().all()
    taken = {as_utc(row) for row in rows}
    return [slot for slot in candidates if as_utc(slot) not in taken][:limit]


async def book_slot(
    db: AsyncSession,
    schedule: Schedule,
    *,
    start_text: str,
    name: str,
    phone: str,
    lead_id: int | None,
    notes: str | None = None,
    now: datetime | None = None,
) -> Appointment:
    """Book one slot or raise SlotError with a message safe to read to the caller."""
    now = now or datetime.now(UTC)
    start_local = parse_local(schedule, start_text)

    if not is_bookable_start(schedule, start_local, now):
        raise SlotError(
            "That time is not an available slot (check working hours, notice time "
            "and how far ahead we book). Ask for the free times again."
        )

    start_utc = as_utc(start_local)
    existing = (
        await db.execute(select(Appointment.id).where(Appointment.start_time == start_utc))
    ).first()
    if existing:
        raise SlotError("That time was just taken.")

    appointment = Appointment(
        lead_id=lead_id,
        start_time=start_utc,
        duration_minutes=schedule.slot_minutes,
        name=name,
        phone=phone,
        notes=notes,
    )
    db.add(appointment)
    try:
        await db.commit()
    except IntegrityError as exc:  # two calls booked the same slot at once
        await db.rollback()
        raise SlotError("That time was just taken.") from exc
    await db.refresh(appointment)
    return appointment
