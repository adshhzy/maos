"""API projection and result slimming helpers for sandbox responses."""

import os
import re
from typing import Any

from maos_runtime.sandbox.constants import (
    API_RESULT_LIST_LIMIT,
    API_RESULT_TEXT_LIMIT,
    COMPLETED_WORKFLOW_DISPLAY_LIMIT,
)


def _task_from_state(
    task_id: str,
    state: dict[str, Any],
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "graph_id": state.get("graph_id", task_id),
        "graph_name": state.get("graph_name", task_id),
        "submitted_at": None,
        "status": _normalize_task_status(status),
        "state": _slim_state_for_api(state),
        "result": result,
        "error": error,
        "workflow_id": task_id,
    }

def _task_list_item_for_api(task: dict[str, Any]) -> dict[str, Any]:
    """Return a lightweight task row for dashboards and tabs.

    Full node outputs are available from `/api/tasks/{task_id}`. Keeping them
    out of the list endpoint avoids repeatedly moving large Agent payloads
    through the Web UI refresh loop.
    """
    item = dict(task)

    state = dict(item.get("state") or {})
    state.pop("results", None)
    state.pop("instance_results", None)
    item["state"] = state

    result = item.get("result")
    if isinstance(result, dict):
        result_item = dict(result)
        result_item.pop("results", None)
        result_item.pop("instance_results", None)
        nested_state = result_item.get("state")
        if isinstance(nested_state, dict):
            nested_state_item = dict(nested_state)
            nested_state_item.pop("results", None)
            nested_state_item.pop("instance_results", None)
            result_item["state"] = nested_state_item
        item["result"] = result_item
    return item

def _minimal_task(
    task_id: str,
    status: str,
    error: str | None = None,
) -> dict[str, Any]:
    state = {
        "graph_id": task_id,
        "graph_name": task_id,
        "workflow_status": status,
        "levels": [],
        "edges": [],
        "nodes": [],
        "human_interventions": [],
        "pending_human_interventions": [],
    }
    return _task_from_state(task_id, state, status=status, error=error)

def _state_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "graph_id": result.get("graph_id", ""),
        "graph_name": result.get("graph_name", ""),
        "workflow_status": result.get("status", "completed"),
        "levels": [],
        "edges": [],
        "nodes": [],
    }

def _slim_result_for_api(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if not isinstance(result, dict):
        return {"value": _truncate_api_value(result)}

    slim: dict[str, Any] = {}
    for key in ("graph_id", "graph_name", "graph_type", "status", "node_count", "instance_count", "error"):
        if key in result:
            slim[key] = _truncate_api_value(result.get(key))
    if isinstance(result.get("state"), dict):
        slim["state"] = _slim_state_for_api(result["state"])

    node_results = result.get("results")
    if isinstance(node_results, dict):
        slim["results"] = {
            str(node_id): _slim_result_payload(payload)
            for node_id, payload in node_results.items()
        }
    instance_results = result.get("instance_results")
    if isinstance(instance_results, dict):
        slim["instance_results"] = _slim_instance_results_for_api(instance_results)
    return slim

def _slim_state_for_api(state: dict[str, Any]) -> dict[str, Any]:
    slim = dict(state)
    nodes = slim.get("nodes")
    if isinstance(nodes, list):
        slim["nodes"] = [
            _slim_node_for_api(node)
            if isinstance(node, dict)
            else node
            for node in nodes
        ]
    instances = slim.get("instances")
    if isinstance(instances, list):
        slim["instances"] = instances[-50:]
    instance_results = slim.get("instance_results")
    if isinstance(instance_results, dict):
        slim["instance_results"] = _slim_instance_results_for_api(instance_results)
    return slim


def _slim_node_for_api(node: dict[str, Any]) -> dict[str, Any]:
    slim = dict(node)
    slim["summary"] = _truncate_api_text(node.get("summary"))
    for key in ("hermes_prompt", "claude_prompt", "codex_prompt"):
        value = slim.pop(key, None)
        if isinstance(value, str) and value:
            slim[f"{key}_available"] = True
            slim[f"{key}_chars"] = len(value)
    return slim

def _slim_instance_results_for_api(instance_results: dict[str, Any]) -> dict[str, Any]:
    return {
        str(instance_id): _slim_result_payload(payload)
        for instance_id, payload in instance_results.items()
    }

def _slim_result_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return _truncate_api_value(payload)

    allowed_keys = (
        "status",
        "error",
        "message",
        "summary",
        "artifact",
        "decision",
        "reason",
        "benchmark_id",
        "evaluation_task_id",
        "winner",
        "score_delta",
        "single_score",
        "multi_score",
        "single",
        "multi",
        "report",
        "structured_output",
        "required_changes",
        "confidence",
        "route",
        "order_status",
        "payment_status",
        "risk",
        "percent",
        "count",
        "tracking_number",
        "channel",
        "reservation_id",
        "agent_backend",
        "agent_key",
        "requested_agent_key",
        "context_policy",
        "runtime_profile",
        "execution_mode",
        "agent_id",
        "agent_name",
        "agent_service_task_id",
        "hermes_job_id",
        "hermes_command",
        "hermes_workdir",
        "codex_task_id",
        "codex_pid",
        "codex_command",
        "codex_workdir",
        "claude_task_id",
        "claude_pid",
        "claude_command",
        "claude_workdir",
        "title",
        "latest_comment",
        "stdout",
        "comments",
        "runs",
        "messages",
        "trace",
        "human_intervention_id",
        "human_intervention_type",
        "human_interventions",
        "human_responses",
        "human_request",
        "human_request_id",
        "response",
        "responder",
        "upstream_node_ids",
    )
    slim: dict[str, Any] = {}
    for key in allowed_keys:
        if key not in payload:
            continue
        if key in {"comments", "runs", "messages", "trace"}:
            slim[key] = _slim_api_list(payload.get(key))
        else:
            slim[key] = _truncate_api_value(payload.get(key))
    omitted_keys = sorted(set(payload) - set(slim))
    if omitted_keys:
        slim["_omitted_keys"] = omitted_keys[:20]
    return slim

def _slim_api_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [_truncate_api_value(item) for item in value[-API_RESULT_LIST_LIMIT:]]

def _truncate_api_value(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate_api_text(value)
    if isinstance(value, list):
        return [_truncate_api_value(item) for item in value[-API_RESULT_LIST_LIMIT:]]
    if isinstance(value, dict):
        return {
            str(key): _truncate_api_value(item)
            for key, item in value.items()
            if str(key) not in {"raw", "history", "full_text", "transcript"}
        }
    return value

def _truncate_api_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= API_RESULT_TEXT_LIMIT:
        return value
    return value[:API_RESULT_TEXT_LIMIT] + "...<truncated>"

def _status_from_execution(execution: Any) -> str:
    if execution.status is None:
        return "running"
    return _normalize_task_status(execution.status.name)

def _status_from_raw_description(description: Any) -> str:
    status = description.raw_description.workflow_execution_info.status
    status_name = status.name if hasattr(status, "name") else str(status)
    return _normalize_task_status(status_name)

def _normalize_task_status(status: str) -> str:
    normalized = status.lower().replace("workflow_execution_status_", "")
    return {
        "running": "running",
        "completed": "completed",
        "failed": "failed",
        "canceled": "cancelled",
        "cancelled": "cancelled",
        "terminated": "terminated",
        "timed_out": "timed_out",
        "timeout": "timed_out",
    }.get(normalized, normalized)

def _truthy_env(name: str) -> bool:
    value = os.environ.get(name, "")
    return value.lower() in {"1", "true", "yes", "on"}

def _completed_workflow_display_limit() -> int:
    value = os.environ.get("SANDBOX_COMPLETED_WORKFLOW_DISPLAY_LIMIT")
    if value is None:
        return COMPLETED_WORKFLOW_DISPLAY_LIMIT
    try:
        return max(0, int(value))
    except ValueError:
        return COMPLETED_WORKFLOW_DISPLAY_LIMIT
