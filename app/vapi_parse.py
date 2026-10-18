"""vapi_parse.py - read Vapi webhook payloads defensively (no framework needed).

Verified against Vapi's server-events docs: a "tool-calls" message carries
`toolCallList` (id + function.name + function.arguments) and we answer with
`{"results": [{"toolCallId", "result" | "error"}]}`. An "end-of-call-report"
carries `call.id`, `endedReason` and `artifact` (transcript, messages,
recording). Fields the docs did not spell out (summary, durationSeconds) are
read if present and tolerated if absent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass(frozen=True)
class CallReport:
    call_id: str
    ended_reason: str
    summary: str
    transcript: str
    duration_seconds: float | None
    recording_url: str


def _args(value: object) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def parse_tool_calls(message: dict) -> list[ToolCall]:
    items = message.get("toolCallList") or []
    if not items:  # fall back to the richer list if the simple one is missing
        items = [
            entry.get("toolCall") or {}
            for entry in (message.get("toolWithToolCallList") or [])
        ]
    calls: list[ToolCall] = []
    for item in items:
        function = item.get("function") or {}
        calls.append(
            ToolCall(
                id=str(item.get("id") or ""),
                name=str(function.get("name") or ""),
                args=_args(function.get("arguments")),
            )
        )
    return calls


def _recording_url(message: dict) -> str:
    artifact = message.get("artifact") or {}
    recording = artifact.get("recording")
    if isinstance(recording, str):
        return recording
    if isinstance(recording, dict):
        for key in ("url", "stereoUrl", "recordingUrl"):
            if isinstance(recording.get(key), str):
                return recording[key]
    fallback = message.get("recordingUrl") or artifact.get("recordingUrl")
    return fallback if isinstance(fallback, str) else ""


def parse_report(message: dict) -> CallReport | None:
    call = message.get("call") or {}
    call_id = call.get("id")
    if not call_id:
        return None
    artifact = message.get("artifact") or {}
    analysis = message.get("analysis") or {}
    duration = message.get("durationSeconds")
    return CallReport(
        call_id=str(call_id),
        ended_reason=str(message.get("endedReason") or ""),
        summary=str(message.get("summary") or analysis.get("summary") or ""),
        transcript=str(artifact.get("transcript") or message.get("transcript") or ""),
        duration_seconds=float(duration) if isinstance(duration, (int, float)) else None,
        recording_url=_recording_url(message),
    )


def classify_outcome(ended_reason: str, has_appointment: bool, interest_level: str | None) -> str:
    """Final lead status once the call has ended."""
    if has_appointment:
        return "booked"
    if interest_level in ("hot", "warm"):
        return "qualified"
    if interest_level in ("cold", "not_interested"):
        return "not_interested"
    reason = (ended_reason or "").lower()
    if any(key in reason for key in ("no-answer", "no answer", "busy", "voicemail", "did-not-answer")):
        return "no_answer"
    if any(key in reason for key in ("error", "fail")):
        return "failed"
    return "completed"
