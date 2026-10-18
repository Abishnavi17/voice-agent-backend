"""Unit tests for the pure logic (no database, no web framework needed).

Run with either:  python -m unittest tests.test_logic     or     pytest tests/test_logic.py
"""

from __future__ import annotations

import os
import unittest
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app import assistant_config
from app.config import parse_window, parse_workdays
from app.phone import normalize_phone
from app.slots import (
    Schedule,
    SlotError,
    candidate_slots,
    format_slot,
    human_slot,
    in_window,
    is_bookable_start,
    parse_local,
    slots_for_day,
)
from app.vapi_parse import classify_outcome, parse_report, parse_tool_calls

UTC = timezone.utc
IST = ZoneInfo("Asia/Kolkata")

SCHEDULE = Schedule(
    tz="Asia/Kolkata",
    open_at=time(9, 0),
    close_at=time(12, 0),
    workdays=(0, 1, 2, 3, 4),  # Mon-Fri
    slot_minutes=30,
    horizon_days=3,
    min_notice_minutes=60,
)
# Monday 2026-10-19, 08:00 IST == 02:30 UTC
MONDAY_8AM = datetime(2026, 10, 19, 8, 0, tzinfo=IST).astimezone(UTC)


class PhoneTests(unittest.TestCase):
    def test_international_number_is_cleaned(self):
        self.assertEqual(normalize_phone("+91 98765-43210"), "+919876543210")

    def test_double_zero_prefix(self):
        self.assertEqual(normalize_phone("0091 9876543210"), "+919876543210")

    def test_national_number_gets_default_country_code(self):
        self.assertEqual(normalize_phone("098765 43210", "+91"), "+919876543210")
        self.assertEqual(normalize_phone("9876543210", "+91"), "+919876543210")

    def test_national_number_without_default_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_phone("9876543210")

    def test_garbage_is_rejected(self):
        for bad in ("", "abc", "+12", "+0123456789", "+" + "9" * 20):
            with self.assertRaises(ValueError, msg=bad):
                normalize_phone(bad, "+91")


class ConfigTests(unittest.TestCase):
    def test_parse_window(self):
        self.assertEqual(parse_window("09:00-18:30"), (time(9, 0), time(18, 30)))

    def test_parse_window_rejects_bad_input(self):
        for bad in ("9-18", "18:00-09:00", "09:00", "09:00-09:00", "ab-cd"):
            with self.assertRaises(ValueError, msg=bad):
                parse_window(bad)

    def test_parse_workdays(self):
        self.assertEqual(parse_workdays("4, 0,1,x,9"), (0, 1, 4))
        self.assertEqual(parse_workdays("nonsense"), (0, 1, 2, 3, 4))


class SlotTests(unittest.TestCase):
    def test_day_slots_respect_hours(self):
        slots = slots_for_day(SCHEDULE, date(2026, 10, 19))
        self.assertEqual([format_slot(s) for s in slots][0], "2026-10-19 09:00")
        self.assertEqual([format_slot(s) for s in slots][-1], "2026-10-19 11:30")
        self.assertEqual(len(slots), 6)

    def test_weekend_has_no_slots(self):
        self.assertEqual(slots_for_day(SCHEDULE, date(2026, 10, 17)), [])  # Saturday

    def test_min_notice_hides_near_slots(self):
        slots = candidate_slots(SCHEDULE, MONDAY_8AM)
        # 08:00 now + 60 min notice -> first slot is 09:00 (exactly at the limit)
        self.assertEqual(format_slot(slots[0]), "2026-10-19 09:00")
        at_830 = candidate_slots(SCHEDULE, MONDAY_8AM + timedelta(minutes=30))  # earliest 09:30
        self.assertEqual(format_slot(at_830[0]), "2026-10-19 09:30")
        at_845 = candidate_slots(SCHEDULE, MONDAY_8AM + timedelta(minutes=45))  # earliest 09:45
        self.assertEqual(format_slot(at_845[0]), "2026-10-19 10:00")

    def test_horizon_limits_days(self):
        days = {s.date() for s in candidate_slots(SCHEDULE, MONDAY_8AM)}
        # Monday + 3 days ahead = Mon, Tue, Wed, Thu
        self.assertEqual(
            days, {date(2026, 10, 19), date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)}
        )

    def test_from_day_skips_earlier_days(self):
        slots = candidate_slots(SCHEDULE, MONDAY_8AM, from_day=date(2026, 10, 21))
        self.assertEqual(format_slot(slots[0]), "2026-10-21 09:00")

    def test_parse_local_accepts_common_formats(self):
        for text in ("2026-10-20 15:00", "2026-10-20T15:00", "2026-10-20T15:00:00+05:30",
                     "2026-10-20T09:30:00Z"):
            self.assertEqual(format_slot(parse_local(SCHEDULE, text)), "2026-10-20 15:00")

    def test_parse_local_rejects_nonsense(self):
        for bad in ("", "tomorrow at 3", "2026-13-40 10:00"):
            with self.assertRaises(SlotError, msg=bad):
                parse_local(SCHEDULE, bad)

    def test_is_bookable_start(self):
        ok = parse_local(SCHEDULE, "2026-10-20 10:00")
        self.assertTrue(is_bookable_start(SCHEDULE, ok, MONDAY_8AM))
        for text in (
            "2026-10-20 10:15",  # off the 30-minute grid
            "2026-10-20 12:00",  # closing time, no room for a slot
            "2026-10-20 08:30",  # before opening
            "2026-10-24 10:00",  # Saturday
            "2026-10-30 10:00",  # beyond the booking horizon
            "2026-10-19 08:30",  # in the past / inside the notice period
        ):
            self.assertFalse(is_bookable_start(SCHEDULE, parse_local(SCHEDULE, text), MONDAY_8AM), text)

    def test_human_slot(self):
        self.assertEqual(human_slot(datetime(2026, 10, 20, 15, 0, tzinfo=IST)), "Tue 20 Oct, 3:00 PM")
        self.assertEqual(human_slot(datetime(2026, 10, 20, 0, 30, tzinfo=IST)), "Tue 20 Oct, 12:30 AM")
        self.assertEqual(human_slot(datetime(2026, 10, 20, 12, 0, tzinfo=IST)), "Tue 20 Oct, 12:00 PM")

    def test_in_window(self):
        at_10_ist = datetime(2026, 10, 19, 10, 0, tzinfo=IST).astimezone(UTC)
        at_22_ist = datetime(2026, 10, 19, 22, 0, tzinfo=IST).astimezone(UTC)
        self.assertTrue(in_window(time(9, 0), time(20, 0), at_10_ist, "Asia/Kolkata"))
        self.assertFalse(in_window(time(9, 0), time(20, 0), at_22_ist, "Asia/Kolkata"))


class WebhookParseTests(unittest.TestCase):
    def test_tool_calls_with_object_arguments(self):
        message = {
            "type": "tool-calls",
            "toolCallList": [
                {"id": "t1", "type": "function",
                 "function": {"name": "check_availability", "arguments": {"date": "2026-10-20"}}}
            ],
        }
        (call,) = parse_tool_calls(message)
        self.assertEqual((call.id, call.name, call.args), ("t1", "check_availability", {"date": "2026-10-20"}))

    def test_tool_calls_with_string_arguments(self):
        message = {"toolCallList": [
            {"id": "t2", "function": {"name": "book_appointment", "arguments": '{"start_time": "x"}'}}]}
        self.assertEqual(parse_tool_calls(message)[0].args, {"start_time": "x"})

    def test_tool_calls_with_broken_arguments_become_empty(self):
        message = {"toolCallList": [{"id": "t3", "function": {"name": "n", "arguments": "{oops"}}]}
        self.assertEqual(parse_tool_calls(message)[0].args, {})

    def test_tool_calls_fall_back_to_rich_list(self):
        message = {"toolWithToolCallList": [
            {"toolCall": {"id": "t4", "function": {"name": "save_qualification", "arguments": {}}}}]}
        self.assertEqual(parse_tool_calls(message)[0].name, "save_qualification")

    def test_report_full(self):
        message = {
            "type": "end-of-call-report",
            "endedReason": "customer-ended-call",
            "summary": "Wants solar, booked.",
            "durationSeconds": 61.2,
            "artifact": {"transcript": "AI: hi", "recording": {"url": "https://r/1.wav"}},
            "call": {"id": "c1"},
        }
        report = parse_report(message)
        self.assertEqual(
            (report.call_id, report.ended_reason, report.summary, report.transcript,
             report.duration_seconds, report.recording_url),
            ("c1", "customer-ended-call", "Wants solar, booked.", "AI: hi", 61.2, "https://r/1.wav"),
        )

    def test_report_tolerates_missing_fields(self):
        report = parse_report({"call": {"id": "c2"}})
        self.assertEqual((report.summary, report.transcript, report.duration_seconds, report.recording_url),
                         ("", "", None, ""))
        self.assertIsNone(parse_report({"call": {}}))

    def test_report_summary_from_analysis(self):
        report = parse_report({"call": {"id": "c3"}, "analysis": {"summary": "short"}})
        self.assertEqual(report.summary, "short")

    def test_classify_outcome(self):
        self.assertEqual(classify_outcome("hangup", True, None), "booked")
        self.assertEqual(classify_outcome("hangup", False, "hot"), "qualified")
        self.assertEqual(classify_outcome("hangup", False, "warm"), "qualified")
        self.assertEqual(classify_outcome("hangup", False, "cold"), "not_interested")
        self.assertEqual(classify_outcome("customer-did-not-answer", False, None), "no_answer")
        self.assertEqual(classify_outcome("customer-busy", False, None), "no_answer")
        self.assertEqual(classify_outcome("twilio-failed-to-connect-call", False, None), "failed")
        self.assertEqual(classify_outcome("customer-ended-call", False, None), "completed")


class AssistantConfigTests(unittest.TestCase):
    def test_tool_payloads_point_at_the_webhook(self):
        payloads = assistant_config.build_tool_payloads("https://x.example.com/", "cred1")
        self.assertEqual([p["function"]["name"] for p in payloads], assistant_config.TOOL_NAMES)
        for payload in payloads:
            self.assertEqual(payload["type"], "function")
            self.assertEqual(payload["server"],
                             {"url": "https://x.example.com/vapi/webhook", "credentialId": "cred1"})

    def test_credential_is_optional(self):
        payload = assistant_config.build_tool_payloads("https://x.example.com")[0]
        self.assertNotIn("credentialId", payload["server"])

    def test_assistant_payload_references_tools_and_prompt(self):
        body = assistant_config.build_assistant_payload("https://x.example.com", ["a", "b", "c"], "cred1")
        self.assertEqual(body["model"]["toolIds"], ["a", "b", "c"])
        self.assertEqual(body["model"]["messages"][0]["content"], assistant_config.SYSTEM_PROMPT)
        self.assertEqual(body["server"]["url"], "https://x.example.com/vapi/webhook")

    def test_prompt_variables_are_all_supplied_by_the_backend(self):
        import re
        needed = set(re.findall(r"\{\{\s*(\w+)\s*\}\}", assistant_config.SYSTEM_PROMPT + assistant_config.FIRST_MESSAGE))
        builtin = {"now"}  # Vapi fills {{now}} itself
        supplied = {"name", "business_name", "lead_context", "timezone"}  # app/leads.py place_call()
        self.assertEqual(needed - builtin, supplied)

    def test_tool_names_match_webhook_handlers(self):
        path = os.path.join(os.path.dirname(__file__), "..", "app", "vapi_webhook.py")
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for name in assistant_config.TOOL_NAMES:
            self.assertIn(f'call.name == "{name}"', source)

    def test_required_params_are_declared_properties(self):
        for tool in assistant_config.TOOLS:
            params = tool["parameters"]
            for field in params["required"]:
                self.assertIn(field, params["properties"])


if __name__ == "__main__":
    unittest.main()
