"""slots.py - pure appointment-slot math (no database, no web framework).

Everything here takes plain values so it is easy to unit-test with a fixed
"now". The database side (which slots are already taken, saving a booking)
lives in scheduling.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
SLOT_FORMAT = "%Y-%m-%d %H:%M"


class SlotError(Exception):
    """A requested time cannot be booked. The message is safe to read to a caller."""


@dataclass(frozen=True)
class Schedule:
    tz: str  # IANA name, e.g. "Asia/Kolkata"
    open_at: time
    close_at: time
    workdays: tuple[int, ...]  # Monday = 0
    slot_minutes: int
    horizon_days: int
    min_notice_minutes: int


def as_utc(value: datetime) -> datetime:
    """Treat naive datetimes (SQLite drops tzinfo) as UTC; convert aware ones."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def slots_for_day(schedule: Schedule, day: date) -> list[datetime]:
    """Every slot start on one local day, ignoring notice, horizon and bookings."""
    if day.weekday() not in schedule.workdays:
        return []
    tz = ZoneInfo(schedule.tz)
    cursor = datetime.combine(day, schedule.open_at, tzinfo=tz)
    end = datetime.combine(day, schedule.close_at, tzinfo=tz)
    step = timedelta(minutes=schedule.slot_minutes)
    slots: list[datetime] = []
    while cursor + step <= end:
        slots.append(cursor)
        cursor += step
    return slots


def candidate_slots(
    schedule: Schedule, now_utc: datetime, from_day: date | None = None
) -> list[datetime]:
    """Bookable slot starts (local-time aware datetimes), earliest first.

    Honors working days/hours, the minimum notice, and the booking horizon.
    Existing bookings are NOT considered here.
    """
    tz = ZoneInfo(schedule.tz)
    today = now_utc.astimezone(tz).date()
    first = max(from_day, today) if from_day else today
    last = today + timedelta(days=schedule.horizon_days)
    earliest = now_utc + timedelta(minutes=schedule.min_notice_minutes)

    result: list[datetime] = []
    day = first
    while day <= last:
        result.extend(s for s in slots_for_day(schedule, day) if as_utc(s) >= earliest)
        day += timedelta(days=1)
    return result


def parse_local(schedule: Schedule, text: str) -> datetime:
    """Parse '2026-10-20 15:00' (or ISO with 'T' / offset) into a local aware datetime."""
    raw = (text or "").strip()
    if not raw:
        raise SlotError("No time was given. Use the format YYYY-MM-DD HH:MM.")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise SlotError(
            f"Could not understand the time {raw!r}. Use the format YYYY-MM-DD HH:MM."
        ) from exc
    tz = ZoneInfo(schedule.tz)
    parsed = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    return parsed.replace(second=0, microsecond=0)


def format_slot(slot_local: datetime) -> str:
    """Machine string the agent passes back to book_appointment."""
    return slot_local.strftime(SLOT_FORMAT)


def human_slot(slot_local: datetime) -> str:
    """'Tue 20 Oct, 3:00 PM' - platform-independent (no %-I)."""
    hour12 = slot_local.hour % 12 or 12
    suffix = "AM" if slot_local.hour < 12 else "PM"
    return f"{slot_local:%a} {slot_local.day} {slot_local:%b}, {hour12}:{slot_local:%M} {suffix}"


def is_bookable_start(schedule: Schedule, start_local: datetime, now_utc: datetime) -> bool:
    """True if start_local is exactly one of the schedule's bookable slot starts."""
    wanted = as_utc(start_local)
    return any(
        as_utc(slot) == wanted
        for slot in candidate_slots(schedule, now_utc, start_local.date())
        if slot.date() == start_local.date()
    )


def in_window(open_at: time, close_at: time, now_utc: datetime, tz_name: str) -> bool:
    """Is the current local time within [open_at, close_at] (minute precision)?"""
    local = now_utc.astimezone(ZoneInfo(tz_name)).time().replace(second=0, microsecond=0)
    return open_at <= local <= close_at
