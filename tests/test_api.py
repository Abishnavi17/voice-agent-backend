"""End-to-end API tests: lead intake -> outbound call -> agent tool calls -> call report.

Vapi itself is faked (see conftest.fake_vapi); everything else is the real app
running on SQLite. Run with:  pip install -r requirements-dev.txt && pytest
"""

from __future__ import annotations

import re

from app.vapi_client import VapiError

API = {"X-API-Key": "test-api-key"}
HOOK = {"X-Vapi-Secret": "test-webhook-secret"}
SLOT = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def lead_payload(**overrides):
    payload = {
        "name": "Priya",
        "phone": "+919876543210",
        "source": "Meta lead form",
        "message": "Need rooftop solar",
        "consent": True,
    }
    payload.update(overrides)
    return payload


def make_lead(client, phone="+919876543210"):
    response = client.post("/leads", json=lead_payload(phone=phone), headers=API)
    assert response.status_code == 202, response.text
    return response.json()["lead_id"]


def call_tool(client, call_id, name, arguments):
    body = {
        "message": {
            "type": "tool-calls",
            "call": {"id": call_id},
            "toolCallList": [
                {"id": "tc-1", "type": "function", "function": {"name": name, "arguments": arguments}}
            ],
        }
    }
    response = client.post("/vapi/webhook", json=body, headers=HOOK)
    assert response.status_code == 200, response.text
    return response.json()["results"][0]


def first_slot(client, call_id):
    result = call_tool(client, call_id, "check_availability", {})
    return SLOT.search(result["result"]).group(0)


def end_report(client, call_id, **fields):
    message = {"type": "end-of-call-report", "call": {"id": call_id}, **fields}
    response = client.post("/vapi/webhook", json={"message": message}, headers=HOOK)
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------- security


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_lead_endpoints_need_the_api_key(client, fake_vapi):
    assert client.post("/leads", json=lead_payload()).status_code == 401
    assert client.post("/leads", json=lead_payload(), headers={"X-API-Key": "nope"}).status_code == 401
    assert client.get("/leads").status_code == 401
    assert fake_vapi == []


def test_endpoints_fail_closed_when_secrets_are_not_configured(client, monkeypatch):
    monkeypatch.setenv("LEADS_API_KEY", "")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    assert client.get("/leads", headers=API).status_code == 503
    assert client.post("/vapi/webhook", json={}, headers=HOOK).status_code == 503


def test_webhook_secret_accepted_as_bearer_or_header_only(client):
    body = {"message": {"type": "status-update"}}
    assert client.post("/vapi/webhook", json=body).status_code == 401
    assert client.post("/vapi/webhook", json=body, headers={"X-Vapi-Secret": "wrong"}).status_code == 401
    assert client.post("/vapi/webhook", json=body, headers=HOOK).status_code == 200
    bearer = {"Authorization": "Bearer test-webhook-secret"}
    assert client.post("/vapi/webhook", json=body, headers=bearer).status_code == 200


# ----------------------------------------------------------------- intake


def test_consent_is_required(client, fake_vapi):
    response = client.post("/leads", json=lead_payload(consent=False), headers=API)
    assert response.status_code == 400
    assert fake_vapi == []


def test_bad_phone_is_rejected(client, fake_vapi):
    response = client.post("/leads", json=lead_payload(phone="12"), headers=API)
    assert response.status_code == 422
    assert fake_vapi == []


def test_new_lead_triggers_an_outbound_call(client, fake_vapi):
    response = client.post("/leads", json=lead_payload(), headers=API)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "calling" and body["duplicate"] is False

    (call,) = fake_vapi
    assert call["phone"] == "+919876543210"
    assert call["variables"]["name"] == "Priya"
    assert call["variables"]["business_name"] == "Test Solar"
    assert "Need rooftop solar" in call["variables"]["lead_context"]

    detail = client.get(f"/leads/{body['lead_id']}", headers=API).json()
    assert detail["vapi_call_id"] == "call-1"
    assert detail["call_attempts"] == 1


def test_number_without_country_code_uses_the_default(client, fake_vapi):
    client.post("/leads", json=lead_payload(phone="098765 43210"), headers=API)
    assert fake_vapi[0]["phone"] == "+919876543210"


def test_double_submit_does_not_call_twice(client, fake_vapi):
    first = client.post("/leads", json=lead_payload(), headers=API).json()
    second = client.post("/leads", json=lead_payload(), headers=API).json()
    assert second["duplicate"] is True and second["lead_id"] == first["lead_id"]
    assert len(fake_vapi) == 1


def test_vapi_failure_is_reported_and_the_lead_is_kept(client, monkeypatch):
    async def failing(**kwargs):
        raise VapiError("Vapi returned 401: bad key")

    monkeypatch.setattr("app.leads.start_outbound_call", failing)
    response = client.post("/leads", json=lead_payload(), headers=API)
    assert response.status_code == 502
    failed = client.get("/leads?status=failed", headers=API).json()["leads"]
    assert len(failed) == 1 and "bad key" in failed[0]["summary"]


def test_lead_outside_calling_hours_waits_then_is_called(client, fake_vapi, monkeypatch):
    from app.leads import process_pending

    monkeypatch.setattr("app.leads.calls_allowed_now", lambda now=None: False)
    body = client.post("/leads", json=lead_payload(), headers=API).json()
    assert body["status"] == "pending_window" and fake_vapi == []

    monkeypatch.setattr("app.leads.calls_allowed_now", lambda now=None: True)
    assert client.portal.call(process_pending) == 1
    assert len(fake_vapi) == 1
    assert client.get(f"/leads/{body['lead_id']}", headers=API).json()["status"] == "calling"


# ------------------------------------------------------------ agent tools


def test_agent_can_list_free_times(client, fake_vapi):
    make_lead(client)
    result = call_tool(client, "call-1", "check_availability", {})
    assert "result" in result and SLOT.search(result["result"])


def test_agent_books_an_appointment_and_double_booking_is_refused(client, fake_vapi):
    make_lead(client, "+919876543210")
    make_lead(client, "+919876543211")
    slot = first_slot(client, "call-1")

    booked = call_tool(client, "call-1", "book_appointment", {"start_time": slot})
    assert "Booked" in booked["result"]

    clash = call_tool(client, "call-2", "book_appointment", {"start_time": slot})
    assert "error" in clash and "taken" in clash["error"]
    assert slot not in call_tool(client, "call-2", "check_availability", {})["result"]

    appointments = client.get("/appointments", headers=API).json()["appointments"]
    assert len(appointments) == 1 and appointments[0]["phone"] == "+919876543210"


def test_agent_cannot_book_a_time_that_is_not_a_slot(client, fake_vapi):
    make_lead(client)
    for bad in ("2020-01-01 10:00", "not a time", ""):
        result = call_tool(client, "call-1", "book_appointment", {"start_time": bad})
        assert "error" in result, bad
    assert client.get("/appointments", headers=API).json()["appointments"] == []


def test_arguments_may_arrive_as_a_json_string(client, fake_vapi):
    make_lead(client)
    slot = first_slot(client, "call-1")
    result = call_tool(client, "call-1", "book_appointment", f'{{"start_time": "{slot}"}}')
    assert "Booked" in result["result"]


def test_unknown_tool_returns_an_error_entry(client, fake_vapi):
    make_lead(client)
    assert "error" in call_tool(client, "call-1", "delete_everything", {})


def test_hot_lead_is_saved_and_alerts_the_team(client, fake_vapi, monkeypatch):
    events: list[str] = []

    async def record(event, text, data=None):
        events.append(event)

    monkeypatch.setattr("app.vapi_webhook.notify", record)
    lead_id = make_lead(client)
    result = call_tool(
        client, "call-1", "save_qualification",
        {"interest_level": "hot", "service_interest": "5kW rooftop", "budget": "3 lakh"},
    )
    assert result["result"] == "Saved."
    detail = client.get(f"/leads/{lead_id}", headers=API).json()
    assert detail["status"] == "qualified" and detail["interest_level"] == "hot"
    assert detail["qualification"]["budget"] == "3 lakh"
    assert events == ["hot_lead"]


def test_invalid_interest_level_is_rejected(client, fake_vapi):
    make_lead(client)
    assert "error" in call_tool(client, "call-1", "save_qualification", {"interest_level": "maybe"})


# --------------------------------------------------------- call reports


def test_end_of_call_report_is_stored_once_and_sets_the_final_status(client, fake_vapi):
    lead_id = make_lead(client)
    slot = first_slot(client, "call-1")
    call_tool(client, "call-1", "book_appointment", {"start_time": slot})

    report = {
        "endedReason": "customer-ended-call",
        "summary": "Wants solar; booked a consultation.",
        "durationSeconds": 54.5,
        "artifact": {"transcript": "AI: Hi\nUser: Hello"},
    }
    end_report(client, "call-1", **report)
    end_report(client, "call-1", **report)  # Vapi may redeliver

    detail = client.get(f"/leads/{lead_id}", headers=API).json()
    assert detail["status"] == "booked"
    assert detail["summary"] == "Wants solar; booked a consultation."
    assert len(detail["call_logs"]) == 1
    assert detail["call_logs"][0]["transcript"] == "AI: Hi\nUser: Hello"
    assert detail["call_logs"][0]["duration_seconds"] == 54.5


def test_unanswered_call_can_be_retried(client, fake_vapi):
    lead_id = make_lead(client)
    end_report(client, "call-1", endedReason="customer-did-not-answer")
    assert client.get(f"/leads/{lead_id}", headers=API).json()["status"] == "no_answer"

    retry = client.post(f"/leads/{lead_id}/call", headers=API)
    assert retry.status_code == 202 and len(fake_vapi) == 2


def test_cannot_start_a_second_call_while_one_is_in_progress(client, fake_vapi):
    lead_id = make_lead(client)
    assert client.post(f"/leads/{lead_id}/call", headers=API).status_code == 409
    assert len(fake_vapi) == 1


def test_unknown_message_types_are_acknowledged(client):
    body = {"message": {"type": "speech-update"}}
    assert client.post("/vapi/webhook", json=body, headers=HOOK).json() == {"ok": True}
