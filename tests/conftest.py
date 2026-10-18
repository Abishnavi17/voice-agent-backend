"""Shared test setup. Settings are put in the environment BEFORE the app is imported,
and the app runs against a throwaway SQLite file (no Postgres/Redis/Vapi needed)."""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

_TMP_DIR = tempfile.mkdtemp(prefix="voice-agent-tests-")
DB_PATH = os.path.join(_TMP_DIR, "test.db")

os.environ.update(
    {
        "DATABASE_URL": f"sqlite+aiosqlite:///{DB_PATH}",
        "LEADS_API_KEY": "test-api-key",
        "WEBHOOK_SECRET": "test-webhook-secret",
        "VAPI_API_KEY": "test-vapi-key",
        "VAPI_ASSISTANT_ID": "asst-test",
        "VAPI_PHONE_NUMBER_ID": "phone-test",
        "BUSINESS_NAME": "Test Solar",
        "BUSINESS_TZ": "Asia/Kolkata",
        "BUSINESS_HOURS": "09:00-18:00",
        "WORKDAYS": "0,1,2,3,4,5,6",
        "SLOT_MINUTES": "30",
        "BOOKING_HORIZON_DAYS": "7",
        "MIN_NOTICE_MINUTES": "60",
        "CALL_WINDOW": "00:00-23:59",
        "DEFAULT_COUNTRY_CODE": "+91",
        "DEDUPE_MINUTES": "10",
        "NOTIFY_WEBHOOK_URL": "",
    }
)

_TABLES = ("appointments", "call_logs", "leads", "turns", "conversations", "sessions")


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client

    connection = sqlite3.connect(DB_PATH)
    try:
        for table in _TABLES:
            try:
                connection.execute(f"DELETE FROM {table}")
            except sqlite3.OperationalError:
                pass
        connection.commit()
    finally:
        connection.close()


@pytest.fixture()
def fake_vapi(monkeypatch):
    """Replace the real Vapi call with a recorder. Returns the list of calls made."""
    calls: list[dict] = []

    async def fake_start_outbound_call(**kwargs):
        calls.append(kwargs)
        return f"call-{len(calls)}"

    monkeypatch.setattr("app.leads.start_outbound_call", fake_start_outbound_call)
    return calls
