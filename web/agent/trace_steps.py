"""Convert raw Agent runtime messages into normalized trace steps."""

import json
from typing import Any

from web.agent.normalization import trim_text


def build_trace_steps(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(messages, key=lambda message: int(message.get("seq") or 0))
    steps = []
    for message in ordered:
        text = message_text(message)
        kind = message_kind(message)
        created_at = (
            message.get("created_at")
            or message.get("timestamp")
            or message.get("time")
            or message.get("ts")
        )
        step = {
            "seq": message.get("seq"),
            "type": message.get("type") or kind,
            "kind": kind,
            "tool": message.get("tool") or message.get("name") or "",
            "status": message.get("status") or message.get("state") or "",
            "created_at": created_at,
            "chars": len(text),
            "approx_tokens": max(0, (len(text) + 3) // 4),
            "preview": trim_text(text, 2400),
        }
        exit_code = message.get("exit_code") or message.get("return_code")
        if exit_code is not None:
            step["exit_code"] = exit_code
        steps.append(step)
    return steps

def message_kind(message: dict[str, Any]) -> str:
    message_type = str(message.get("type") or "").lower()
    tool = str(message.get("tool") or message.get("name") or "").lower()
    if message_type == "tool_use":
        return f"tool_use:{tool or 'tool'}"
    if message_type == "tool_result":
        return f"tool_result:{tool or 'tool'}"
    if "terminal" in tool or "shell" in tool:
        return "terminal"
    if "python" in tool:
        return "python"
    if message_type:
        return message_type
    return "message"

def message_text(message: dict[str, Any]) -> str:
    for key in ("text", "content", "output", "body", "message"):
        value = message.get(key)
        if isinstance(value, str):
            return value
    for key in ("input", "result", "payload"):
        value = message.get(key)
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            for nested_key in ("text", "content", "output", "command", "code"):
                nested = value.get(nested_key)
                if isinstance(nested, str):
                    return nested
    return json.dumps(message, ensure_ascii=False, default=str)
