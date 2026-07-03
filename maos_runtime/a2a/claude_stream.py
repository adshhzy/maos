"""Readable projection helpers for Claude CLI stream-json output."""

from __future__ import annotations

import json
from typing import Any


def stream_json_events(stdout: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    if events:
        return events
    try:
        event = json.loads(stdout)
    except json.JSONDecodeError:
        return events
    return [event] if isinstance(event, dict) else events


def stream_json_terminal_event(stdout: str) -> dict[str, Any] | None:
    for event in reversed(stream_json_events(stdout)):
        if event.get("type") == "result" or "terminal_reason" in event:
            return event
    return None


def stream_json_final_output(stdout: str) -> str | None:
    terminal = stream_json_terminal_event(stdout)
    if terminal is not None and isinstance(terminal.get("result"), str):
        return terminal["result"]
    return None


def readable_claude_partial_output(stdout: str) -> str:
    """Return visible assistant text accumulated so far, without raw JSONL."""

    pieces = [
        str(step.get("preview") or "")
        for step in readable_claude_trace_from_stream(stdout)
        if step.get("type") == "claude.text" and str(step.get("preview") or "").strip()
    ]
    return "\n\n".join(pieces).strip()


def readable_claude_trace_from_stream(
    stdout: str,
    *,
    started_at: Any = None,
    finished_at: Any = None,
    elapsed: float | None = None,
    include_lifecycle: bool = False,
) -> list[dict[str, Any]]:
    events = stream_json_events(stdout)
    has_nested_stream_events = any(event.get("type") == "stream_event" for event in events)
    trace: list[dict[str, Any]] = []
    text_buffers: dict[str, list[str]] = {}
    stream_blocks: dict[int, dict[str, Any]] = {}
    first_timestamp: Any = None

    def flush_text_buffers() -> None:
        for part_type in ("thinking", "text"):
            pieces = text_buffers.get(part_type) or []
            text = "".join(pieces).strip()
            if not text:
                continue
            trace.append(
                {
                    "type": f"claude.{part_type}",
                    "title": (
                        "Claude reasoning / progress text"
                        if part_type == "thinking"
                        else "Claude generated assistant text"
                    ),
                    "description": (
                        "Streaming chunks were merged into one readable block."
                        if len(pieces) > 1
                        else "Claude emitted a readable text block."
                    ),
                    "timestamp": first_timestamp,
                    "preview": _truncate(text, 6000),
                    "chunk_count": len(pieces),
                    "chars": len(text),
                    "aggregated": True,
                }
            )
        text_buffers.clear()

    def flush_stream_block(index: int) -> None:
        block = stream_blocks.pop(index, None)
        if not block:
            return
        block_type = str(block.get("type") or "text")
        pieces = block.get("pieces") if isinstance(block.get("pieces"), list) else []
        text = "".join(str(item) for item in pieces).strip()
        timestamp = block.get("timestamp") or first_timestamp
        if block_type in {"text", "thinking"}:
            if not text:
                return
            trace.append(
                {
                    "type": f"claude.{block_type}",
                    "title": (
                        "Claude reasoning / progress text"
                        if block_type == "thinking"
                        else "Claude generated assistant text"
                    ),
                    "description": "Streaming deltas were merged into one readable block.",
                    "timestamp": timestamp,
                    "preview": _truncate(text, 6000),
                    "chunk_count": len(pieces),
                    "chars": len(text),
                    "aggregated": True,
                    "stream_block_index": index,
                }
            )
            return
        if block_type == "tool_use":
            tool_input = block.get("input") or _parse_partial_json(
                "".join(str(item) for item in block.get("input_json", []))
            )
            trace.append(
                {
                    "type": "claude.tool_use",
                    "title": f"Claude requested tool: {block.get('name') or 'tool'}",
                    "description": _tool_use_description(block.get("name"), tool_input),
                    "timestamp": timestamp,
                    "tool": block.get("name"),
                    "preview": _truncate(_compact_json(tool_input), 3000),
                    "stream_block_index": index,
                }
            )

    def flush_all_stream_blocks() -> None:
        for index in sorted(list(stream_blocks)):
            flush_stream_block(index)

    if include_lifecycle:
        trace.append(
            {
                "type": "claude.submit",
                "title": "Started Claude CLI one-shot task",
                "description": "Claude CLI process was started and received the node prompt on stdin.",
                "timestamp": started_at,
            }
        )

    for index, event in enumerate(events, start=1):
        outer_type = str(event.get("type") or "event")
        inner_event = event.get("event") if outer_type == "stream_event" and isinstance(event.get("event"), dict) else event
        event_type = str(inner_event.get("type") or outer_type)
        timestamp = event.get("timestamp") or event.get("created_at") or inner_event.get("timestamp") or inner_event.get("created_at")
        if first_timestamp is None and timestamp is not None:
            first_timestamp = timestamp

        if outer_type == "system":
            subtype = str(event.get("subtype") or "")
            if subtype not in {"init", ""}:
                continue
            if not any(step.get("type") == "claude.system" for step in trace):
                trace.append(
                    {
                        "type": "claude.system",
                        "title": "Claude session initialized",
                        "description": "Claude CLI emitted session metadata.",
                        "timestamp": timestamp,
                        "session_id": event.get("session_id"),
                        "model": event.get("model"),
                        "cwd": event.get("cwd"),
                    }
                )
            continue

        if outer_type == "stream_event":
            if _handle_nested_stream_event(inner_event, timestamp, stream_blocks, flush_stream_block):
                continue
            if event_type in {"message_start", "message_delta", "message_stop", "content_block_stop"}:
                continue
        elif has_nested_stream_events and outer_type == "assistant":
            # Claude CLI emits both fine-grained stream_event deltas and a
            # replay-style assistant message. The stream deltas are the source
            # of truth for timeline rendering; parsing both doubles each step.
            continue

        if event_type == "result" or "terminal_reason" in inner_event:
            flush_text_buffers()
            flush_all_stream_blocks()
            trace.append(_terminal_step(inner_event, timestamp, elapsed))
            continue

        content = _event_content(inner_event)
        if isinstance(content, list):
            for part_index, part in enumerate(content, start=1):
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or event_type)
                if part_type in {"text", "thinking"}:
                    text = _part_text(part)
                    if text:
                        text_buffers.setdefault(part_type, []).append(text)
                    continue
                flush_text_buffers()
                step = _structured_part_step(part, event_type, timestamp, index, part_index)
                if step:
                    trace.append(step)
            continue

        text = _event_text_delta(inner_event)
        if text:
            text_buffers.setdefault("text", []).append(text)
            continue

        if event_type in {"assistant", "user"}:
            continue
        flush_text_buffers()
        trace.append(
            {
                "type": f"claude.{event_type}",
                "title": _event_title(inner_event),
                "description": _event_description(inner_event),
                "timestamp": timestamp,
                "preview": _truncate(_compact_json(inner_event), 1200),
            }
        )

    flush_text_buffers()
    flush_all_stream_blocks()
    if include_lifecycle:
        trace.append(
            {
                "type": "claude.complete",
                "title": "Claude CLI returned final output",
                "description": "Claude CLI reached a terminal result event and the final answer was extracted.",
                "timestamp": finished_at,
                "duration_seconds": elapsed,
            }
        )
    return trace


def _handle_nested_stream_event(
    event: dict[str, Any],
    timestamp: Any,
    stream_blocks: dict[int, dict[str, Any]],
    flush_stream_block: Any,
) -> bool:
    event_type = str(event.get("type") or "")
    index = int(event.get("index") or 0)
    if event_type == "content_block_start":
        content_block = event.get("content_block") if isinstance(event.get("content_block"), dict) else {}
        block_type = str(content_block.get("type") or "text")
        block = {
            "type": block_type,
            "timestamp": timestamp,
            "pieces": [],
            "input_json": [],
        }
        if block_type == "thinking":
            block["pieces"].append(_string_value(content_block.get("thinking")))
        elif block_type == "text":
            block["pieces"].append(_string_value(content_block.get("text")))
        elif block_type == "tool_use":
            block["name"] = content_block.get("name")
            block["id"] = content_block.get("id")
            if content_block.get("input") is not None:
                block["input"] = content_block.get("input")
        stream_blocks[index] = block
        return True
    if event_type == "content_block_delta":
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        block = stream_blocks.setdefault(
            index,
            {"type": _block_type_from_delta(delta), "timestamp": timestamp, "pieces": [], "input_json": []},
        )
        delta_type = str(delta.get("type") or "")
        if delta_type == "thinking_delta":
            block["type"] = "thinking"
            block.setdefault("pieces", []).append(_string_value(delta.get("thinking")))
        elif delta_type == "text_delta":
            block["type"] = "text"
            block.setdefault("pieces", []).append(_string_value(delta.get("text")))
        elif delta_type == "input_json_delta":
            block["type"] = "tool_use"
            block.setdefault("input_json", []).append(_string_value(delta.get("partial_json")))
        return True
    if event_type == "content_block_stop":
        flush_stream_block(index)
        return True
    return False


def _block_type_from_delta(delta: dict[str, Any]) -> str:
    delta_type = str(delta.get("type") or "")
    if delta_type == "thinking_delta":
        return "thinking"
    if delta_type == "input_json_delta":
        return "tool_use"
    return "text"


def _event_content(event: dict[str, Any]) -> Any:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    if isinstance(message.get("content"), list):
        return message["content"]
    return event.get("content")


def _event_text_delta(event: dict[str, Any]) -> str:
    delta = event.get("delta")
    if isinstance(delta, dict):
        return _string_value(delta.get("text") or delta.get("thinking") or delta.get("content"))
    return _string_value(event.get("text"))


def _part_text(part: dict[str, Any]) -> str:
    return _string_value(part.get("text") or part.get("thinking") or part.get("content"))


def _structured_part_step(
    part: dict[str, Any],
    event_type: str,
    timestamp: Any,
    index: int,
    part_index: int,
) -> dict[str, Any] | None:
    part_type = str(part.get("type") or event_type)
    tool_name = part.get("name") or part.get("tool_name")
    if part_type == "tool_use":
        tool_input = part.get("input")
        return {
            "type": "claude.tool_use",
            "title": f"Claude requested tool: {tool_name or 'tool'}",
            "description": _tool_use_description(tool_name, tool_input),
            "timestamp": timestamp,
            "tool": tool_name,
            "stream_index": index,
            "part_index": part_index,
            "preview": _truncate(_compact_json(tool_input), 3000),
        }
    if part_type == "tool_result":
        content = part.get("content")
        return {
            "type": "claude.tool_result",
            "title": f"Tool result: {tool_name or part.get('tool_use_id') or 'tool'}",
            "description": "Claude received the result of a tool execution.",
            "timestamp": timestamp,
            "tool": tool_name,
            "stream_index": index,
            "part_index": part_index,
            "preview": _truncate(_content_text(content) or _compact_json(content), 3000),
        }
    if part_type in {"image", "document"}:
        return {
            "type": f"claude.{part_type}",
            "title": f"Claude stream content: {part_type}",
            "description": "Claude emitted a non-text content block.",
            "timestamp": timestamp,
            "stream_index": index,
            "part_index": part_index,
            "preview": _truncate(_compact_json(part), 1200),
        }
    return None


def _terminal_step(event: dict[str, Any], timestamp: Any, elapsed: float | None) -> dict[str, Any]:
    usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
    result = event.get("result") if isinstance(event.get("result"), str) else ""
    description = "Claude CLI completed and produced the final answer."
    if event.get("is_error") is True or event.get("subtype") == "error":
        description = "Claude CLI completed with an error result."
    return {
        "type": "claude.result",
        "title": "Claude terminal result",
        "description": description,
        "timestamp": timestamp,
        "duration_seconds": elapsed,
        "subtype": event.get("subtype"),
        "terminal_reason": event.get("terminal_reason"),
        "usage": usage,
        "preview": _truncate(result, 3000) if result else _truncate(_compact_json(event), 1200),
        "chars": len(result),
    }


def _tool_use_description(tool_name: Any, tool_input: Any) -> str:
    if str(tool_name or "").lower() == "bash" and isinstance(tool_input, dict):
        command = tool_input.get("command")
        if command:
            return f"Claude prepared a Bash command: {command}"
    return "Claude prepared a tool call."


def _event_title(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    if "hook" in event_type:
        return "Claude hook event"
    return f"Claude stream event: {event_type}"


def _event_description(event: dict[str, Any]) -> str:
    event_type = str(event.get("type") or "event")
    if "hook" in event_type:
        return "Claude CLI emitted a hook event."
    return "Claude CLI emitted a stream-json event."


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces = []
        for item in content:
            if isinstance(item, str):
                pieces.append(item)
            elif isinstance(item, dict):
                pieces.append(_part_text(item))
        return "".join(pieces)
    return ""


def _string_value(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _compact_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(value)


def _parse_partial_json(value: str) -> Any:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit] + "...<truncated>"
