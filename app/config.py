"""config.py - every setting comes from environment variables (see .env.example).

Settings are read each time get_settings() is called (not cached at import), so
tests and `docker compose` env changes take effect without code changes.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time

from app.slots import Schedule


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _int(name: str, default: int, minimum: int = 0) -> int:
    try:
        value = int(_env(name, str(default)))
    except ValueError:
        return default
    return max(value, minimum)


def parse_window(spec: str) -> tuple[time, time]:
    """'09:00-18:00' -> (time(9, 0), time(18, 0)). Raises ValueError if malformed."""
    try:
        start_s, end_s = spec.split("-")
        start = time.fromisoformat(start_s.strip())
        end = time.fromisoformat(end_s.strip())
    except ValueError as exc:
        raise ValueError(f"invalid time window {spec!r}; use HH:MM-HH:MM") from exc
    if start >= end:
        raise ValueError(f"invalid time window {spec!r}; start must be before end")
    return start, end


def parse_workdays(spec: str) -> tuple[int, ...]:
    """'0,1,2,3,4' (Monday=0) -> (0, 1, 2, 3, 4). Bad entries are ignored."""
    days: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            days.append(int(part))
    return tuple(sorted(set(days))) or (0, 1, 2, 3, 4)


@dataclass(frozen=True)
class Settings:
    # --- Vapi (voice platform) ---
    vapi_api_key: str
    vapi_assistant_id: str
    vapi_phone_number_id: str
    vapi_base_url: str
    # --- security ---
    api_key: str  # protects /leads and /appointments (header X-API-Key)
    webhook_secret: str  # protects /vapi/webhook (Vapi sends it back to us)
    # --- the business the agent speaks for ---
    business_name: str
    business_tz: str
    business_hours: str
    workdays: tuple[int, ...]
    slot_minutes: int
    booking_horizon_days: int
    min_notice_minutes: int
    # --- outbound calling rules ---
    call_window: str
    default_country_code: str
    dedupe_minutes: int
    # --- optional alerts (Slack incoming webhook, Zapier, Make, ...) ---
    notify_webhook_url: str

    def schedule(self) -> Schedule:
        open_at, close_at = parse_window(self.business_hours)
        return Schedule(
            tz=self.business_tz,
            open_at=open_at,
            close_at=close_at,
            workdays=self.workdays,
            slot_minutes=self.slot_minutes,
            horizon_days=self.booking_horizon_days,
            min_notice_minutes=self.min_notice_minutes,
        )

    def call_window_times(self) -> tuple[time, time]:
        return parse_window(self.call_window)


def get_settings() -> Settings:
    return Settings(
        vapi_api_key=_env("VAPI_API_KEY"),
        vapi_assistant_id=_env("VAPI_ASSISTANT_ID"),
        vapi_phone_number_id=_env("VAPI_PHONE_NUMBER_ID"),
        vapi_base_url=_env("VAPI_BASE_URL", "https://api.vapi.ai").rstrip("/"),
        api_key=_env("LEADS_API_KEY"),
        webhook_secret=_env("WEBHOOK_SECRET"),
        business_name=_env("BUSINESS_NAME", "our team"),
        business_tz=_env("BUSINESS_TZ", "Asia/Kolkata"),
        business_hours=_env("BUSINESS_HOURS", "09:00-18:00"),
        workdays=parse_workdays(_env("WORKDAYS", "0,1,2,3,4")),
        slot_minutes=_int("SLOT_MINUTES", 30, minimum=5),
        booking_horizon_days=_int("BOOKING_HORIZON_DAYS", 7, minimum=1),
        min_notice_minutes=_int("MIN_NOTICE_MINUTES", 60),
        call_window=_env("CALL_WINDOW", "09:00-20:00"),
        default_country_code=_env("DEFAULT_COUNTRY_CODE"),
        dedupe_minutes=_int("DEDUPE_MINUTES", 10),
        notify_webhook_url=_env("NOTIFY_WEBHOOK_URL"),
    )
