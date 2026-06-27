"""Agent Service response helpers used by A2A providers."""

import time
from typing import Any

from maos_runtime.http_json_client import request_json as _request_json
from maos_runtime.runtime_config import (
    agent_message_text_limit as _agent_message_text_limit,
    agent_result_text_limit as _agent_result_text_limit,
    agent_service_api_base as _agent_service_api_base,
)
from maos_runtime.a2a.text_parsing import _text_from_record, _truncate_text


def _records_from_response(response: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if response is None:
        return []
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    for key in ("data", "issues", "tasks", "items", "results"):
        value = response.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []

def _task_identifier(task: dict[str, Any] | None) -> str | None:
    if not isinstance(task, dict):
        return None
    value = task.get("id") or task.get("task_id") or task.get("issue_id")
    return None if value is None else str(value)

def _latest_run_messages(task_id: str | None, runs: dict[str, Any] | None) -> dict[str, Any] | None:
    run_id = _latest_run_id(runs)
    if not task_id or not run_id:
        return None
    return _safe_agent_service_get(f"/runs/{run_id}/messages", {"issue_id": task_id})

def _latest_run_id(runs_response: dict[str, Any] | None) -> str | None:
    if not runs_response:
        return None
    runs = runs_response.get("data", runs_response)
    if isinstance(runs, dict):
        runs = runs.get("runs") or runs.get("items") or runs.get("data")
    if not isinstance(runs, list) or not runs:
        return None
    latest = max(
        runs,
        key=lambda item: item.get("created_at") or item.get("started_at") or item.get("updated_at") or "",
    )
    value = latest.get("id") or latest.get("task_id") or latest.get("run_id")
    return None if value is None else str(value)

def _latest_comment_text(comments_response: dict[str, Any] | None) -> str | None:
    return _truncate_text(_latest_comment_raw_text(comments_response), _agent_result_text_limit())

def _latest_comment_raw_text(comments_response: dict[str, Any] | None) -> str | None:
    comments = _response_list(comments_response)
    for comment in reversed(comments):
        text = _text_from_record(comment)
        if text:
            return text
    return None

def _compact_comments(comments_response: dict[str, Any] | None) -> list[dict[str, Any]]:
    records = _response_list(comments_response)
    compact = []
    for record in records[-5:]:
        compact.append(
            {
                "id": record.get("id"),
                "author": record.get("author") or record.get("author_name") or record.get("user_name"),
                "created_at": record.get("created_at"),
                "text": _truncate_text(_text_from_record(record), _agent_result_text_limit()),
            }
        )
    return compact

def _compact_runs(runs_response: dict[str, Any] | None) -> list[dict[str, Any]]:
    records = _response_list(runs_response)
    compact = []
    for record in records[-5:]:
        compact.append(
            {
                "id": record.get("id") or record.get("run_id") or record.get("task_id"),
                "status": record.get("status") or record.get("state"),
                "created_at": record.get("created_at"),
                "started_at": record.get("started_at"),
                "finished_at": record.get("finished_at"),
            }
        )
    return compact

def _compact_messages(messages_response: dict[str, Any] | None) -> list[dict[str, Any]]:
    records = _response_list(messages_response)
    compact = []
    for record in records[-10:]:
        compact.append(
            {
                "id": record.get("id") or record.get("message_id"),
                "sequence": record.get("sequence") or record.get("seq") or record.get("sequence_number"),
                "role": record.get("role") or record.get("author") or record.get("type"),
                "text": _truncate_text(_text_from_record(record), _agent_message_text_limit()),
            }
        )
    return compact

def _response_list(response: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if response is None:
        return []
    value: Any = response
    if isinstance(value, dict):
        value = value.get("data", value)
    if isinstance(value, dict):
        for key in ("comments", "messages", "runs", "items", "issues"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []

def _safe_agent_service_get(path: str, params: dict[str, str] | None = None) -> dict[str, Any] | None:
    try:
        return _request_json(_agent_service_api_base(), "GET", path, params=params)
    except Exception:
        return None

def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _timestamp_from_epoch(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(float(value)))
    except Exception:
        return None
