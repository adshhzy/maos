"""Agent event helpers for JSON control-flow workflows."""

from __future__ import annotations

from typing import Any


def result_output_text(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("latest_comment", "stdout", "result", "output", "response"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    structured = payload.get("structured_output")
    if isinstance(structured, dict):
        for key in ("final_answer", "content", "markdown", "summary"):
            value = structured.get(key)
            if isinstance(value, str) and value.strip():
                return value
    return ""


def agent_event_requires_human(event: dict[str, Any]) -> bool:
    task = event.get("a2a_task") or event.get("task") or {}
    state = ((task.get("status") or {}).get("state") or event.get("a2a_state") or "")
    status = str(event.get("status") or "").lower()
    return state == "TASK_STATE_INPUT_REQUIRED" or status in {
        "needs_input",
        "input_required",
        "waiting_human",
        "human_required",
    }


def human_request_from_agent_event(
    event: dict[str, Any],
    a2a_task: dict[str, Any],
) -> dict[str, Any]:
    metadata = a2a_task.get("metadata", {})
    status_message = (a2a_task.get("status") or {}).get("message") or {}
    message_payload: dict[str, Any] = {}
    if isinstance(status_message, dict):
        for part in status_message.get("parts", []):
            if isinstance(part, dict) and isinstance(part.get("data"), dict):
                message_payload = part["data"]
                break
    human_request = (
        event.get("human_request")
        or metadata.get("humanRequest")
        or message_payload.get("human_request")
        or {}
    )
    return dict(human_request)


def agent_human_intervention_id(
    node_spec: dict[str, Any],
    request_id: str,
) -> str:
    safe_request_id = "".join(
        char if char.isalnum() or char in {"-", "_", "#"} else "-"
        for char in request_id
    )
    return f"human-{node_spec['_instance_id']}-agent-{safe_request_id}"
