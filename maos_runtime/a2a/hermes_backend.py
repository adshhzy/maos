"""Direct Hermes A2A provider implementation."""

import copy
import time
import uuid
from typing import Any

from maos_runtime.a2a_constants import HERMES_BACKEND
from maos_runtime.a2a.messages import _agent_message, _artifact_from_result, _dependency_results_from_artifacts, _event_from_a2a_task, _first_data_part
from maos_runtime.a2a.runtime_config_helpers import (
    _csv_value,
    _hermes_context_policy,
    _hermes_execution_mode,
    _hermes_poll_seconds,
    _hermes_prompt_limit,
    _hermes_runtime_profile,
    _node_agent_config,
    _node_result_text_limit,
    _stable_runtime_id,
)
from maos_runtime.a2a.prompt_builders import _hermes_expected_output_hint, _hermes_prompt
from maos_runtime.a2a.agent_service_client import (
    _compact_comments,
    _compact_messages,
    _compact_runs,
    _latest_run_messages,
    _latest_comment_raw_text,
    _safe_agent_service_get,
    _task_identifier,
    _timestamp,
    _timestamp_from_epoch,
)
from maos_runtime.a2a.text_parsing import _extract_structured_agent_output, _json_object_from_text, _metadata_string, _truncate_text
from maos_runtime.a2a_task_store import TASKS as _TASKS, TASKS_LOCK as _TASKS_LOCK, request_idempotency_key as _request_idempotency_key, store_idempotent_task as _store_idempotent_task, store_task as _store_task
from maos_runtime.http_json_client import request_json as _request_json
from maos_runtime.runtime_config import (
    agent_fetch_run_messages as _agent_fetch_run_messages,
    agent_poll_seconds as _agent_poll_seconds,
    agent_result_recent_comments as _agent_result_recent_comments,
    agent_service_api_base as _agent_service_api_base,
)


HERMES_COMPLETED_STATUSES = {"done", "in_review"}
HERMES_FAILED_STATUSES = {"blocked", "cancelled", "canceled"}


def _send_hermes_message(request: dict[str, Any]) -> dict[str, Any]:
    from maos_runtime.a2a.runtime import get_agent_card

    message = request["message"]
    request_metadata = request.get("metadata", {})
    payload = _first_data_part(message)
    node = payload["node"]
    agent = _node_agent_config(node)
    idempotency_key = _request_idempotency_key(request)
    task_id = _stable_runtime_id("a2a-task", node["id"], idempotency_key)
    context_id = message.get("contextId") or f"context-{uuid.uuid4()}"
    workflow_id = request_metadata["workflow_id"]
    dependency_results = _dependency_results_from_artifacts(
        payload.get("dependency_artifacts", [])
    )
    graph_input = payload.get("graph_input", {})
    runtime_profile = _hermes_runtime_profile(node)
    execution_mode = _hermes_execution_mode(node)
    context_policy = _hermes_context_policy(node)
    timeout_seconds = float(agent.get("timeout_seconds") or node.get("timeout_seconds") or 0)
    result_text_limit = _node_result_text_limit(node)
    expected_output = _hermes_expected_output_hint(
        agent.get("prompt")
        or node.get("prompt")
        or node.get("description")
        or "请完成当前业务子任务，并返回最终结果。"
    )
    prompt = _hermes_prompt(
        node,
        dependency_results,
        graph_input,
        workflow_id,
        task_id,
        context_policy,
        runtime_profile,
    )
    create_payload = {
        "title": f"MAOS Hermes Node Task: {node.get('label') or node['id']}",
        "description": prompt,
        "agent_key": agent.get("agent_key") or node.get("agent_key") or "general_chat",
        "agent_id": agent.get("agent_id") or node.get("agent_id"),
        "agent_name": agent.get("agent_name") or node.get("agent_name"),
        "priority": agent.get("priority") or node.get("priority") or "medium",
        "status": "in_progress",
        "project_id": agent.get("project_id") or node.get("project_id"),
        "parent_id": agent.get("parent_id") or node.get("parent_id"),
        "allow_duplicate": bool(agent.get("allow_duplicate", True)),
        "metadata": {
            "maos_task": True,
            "workflow_id": workflow_id,
            "node_id": node["id"],
            "a2a_task_id": task_id,
            "idempotency_key": idempotency_key or "",
            "backend": HERMES_BACKEND,
            "agent_backend": HERMES_BACKEND,
            "agent_key": agent.get("agent_key") or node.get("agent_key") or "general_chat",
            "requested_agent_key": agent.get("agent_key") or node.get("agent_key") or "hermes",
            "context_policy": context_policy,
            "runtime_profile": runtime_profile,
            "execution_mode": "lightweight_hermes_oneshot",
            "completion_mode": "agent_service_polling",
            "expected_output_format": expected_output.get("format") or "",
            "comment_history_policy": "disabled",
            "allowed_skill_slugs": _csv_value(agent.get("skills") or node.get("skills")),
            "hermes_timeout_seconds": timeout_seconds,
            "result_text_limit": result_text_limit,
        },
    }
    create_payload = {key: value for key, value in create_payload.items() if value is not None}
    agent_task = _request_json(
        _agent_service_api_base(),
        "POST",
        "/api/v1/agent-tasks",
        _agent_task_create_payload(
            create_payload,
            backend=HERMES_BACKEND,
            idempotency_key=idempotency_key,
            context={
                "graph_input": graph_input,
                "dependencies": dependency_results,
            },
        ),
    )
    agent_task_id = _task_identifier(agent_task)
    if not agent_task_id:
        raise RuntimeError(f"AgentService Hermes task response did not contain an id: {agent_task!r}")

    now = _timestamp()
    poll_seconds = float(agent.get("poll_seconds", node.get("poll_seconds", _agent_poll_seconds())))
    task = {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "TASK_STATE_WORKING",
            "message": _agent_message(
                task_id,
                context_id,
                {
                    "status": "accepted",
                    "node_id": node["id"],
                    "agent_service_task_id": agent_task_id,
                    "runtime": HERMES_BACKEND,
                },
            ),
            "timestamp": now,
        },
        "artifacts": [],
        "history": [message],
        "metadata": {
            "agentCard": get_agent_card(node),
            "backend": HERMES_BACKEND,
            "idempotencyKey": idempotency_key,
            "completionMode": "polling",
            "nodeId": node["id"],
            "workflow_id": workflow_id,
            "operation": node.get("operation", "agent_task"),
            "referenceTaskIds": message.get("referenceTaskIds", []),
            "agentServiceTaskId": agent_task_id,
            "agentServiceBase": _agent_service_api_base(),
            "hermesPrompt": prompt,
            "agentKey": agent.get("agent_key") or node.get("agent_key") or "hermes",
            "requestedAgentKey": agent.get("agent_key") or node.get("agent_key") or "hermes",
            "contextPolicy": context_policy,
            "runtimeProfile": runtime_profile,
            "executionMode": execution_mode,
            "expectedOutputFormat": expected_output.get("format"),
            "pollSeconds": poll_seconds,
            "resultTextLimit": result_text_limit,
            "plannedDurationSeconds": None,
            "startedAt": time.time(),
            "lastHeartbeatAt": now,
            "heartbeatCount": 0,
            "callbackMode": "temporal-durable-polling-agentservice-hermes-oneshot",
            "transport": {
                "agentServiceApiBase": _agent_service_api_base(),
                "createTask": "POST /api/v1/agent-tasks",
                "getTask": "GET /api/v1/agent-tasks/{task_id}",
                "resume": "POST /api/v1/agent-tasks/{task_id}/resume",
                "cancel": "POST /api/v1/agent-tasks/{task_id}/cancel",
                "artifacts": "GET /api/v1/agent-tasks/{task_id}/artifacts",
                "events": "GET /api/v1/agent-tasks/{task_id}/events",
                "comments": "GET /tasks/{task_id}/comments",
                "runs": "GET /tasks/{task_id}/runs",
            },
        },
    }
    _store_task(task)
    return {"task": copy.deepcopy(task)}


def _agent_task_create_payload(
    create_payload: dict[str, Any],
    *,
    backend: str,
    idempotency_key: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    metadata = dict(create_payload.get("metadata") or {})
    return {
        "idempotency_key": idempotency_key,
        "agent": {
            "backend": backend,
            "agent_key": create_payload.get("agent_key"),
            "agent_id": create_payload.get("agent_id"),
            "agent_name": create_payload.get("agent_name"),
        },
        "input": {
            "title": create_payload["title"],
            "instruction": create_payload.get("description", ""),
            "context": context,
        },
        "runtime": {
            "mode": "async",
            "context_policy": metadata.get("context_policy"),
            "runtime_profile": metadata.get("runtime_profile"),
            "execution_mode": metadata.get("execution_mode"),
            "priority": create_payload.get("priority"),
            "status": create_payload.get("status"),
            "project_id": create_payload.get("project_id"),
            "parent_id": create_payload.get("parent_id"),
            "allow_duplicate": bool(create_payload.get("allow_duplicate", True)),
            "timeout_seconds": metadata.get("hermes_timeout_seconds"),
        },
        "metadata": metadata,
    }


def _poll_hermes_task(task_id: str) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
    agent_task_id = metadata["agentServiceTaskId"]
    task_response = _request_json(_agent_service_api_base(), "GET", f"/api/v1/agent-tasks/{agent_task_id}")
    agent_task = task_response.get("data", task_response)
    status = str(agent_task.get("status") or "").lower()
    now = _timestamp()
    elapsed = round(time.time() - float(metadata.get("startedAt", time.time())), 2)

    with _TASKS_LOCK:
        metadata["heartbeatCount"] += 1
        metadata["elapsedSeconds"] = elapsed
        metadata["lastHeartbeatAt"] = now
        task["status"]["timestamp"] = now
        task["status"]["message"] = _agent_message(
            task_id,
            task["contextId"],
            {
                "status": "working",
                "node_id": metadata["nodeId"],
                "agent_service_task_id": agent_task_id,
                "agent_status": status,
                "elapsed_seconds": elapsed,
            },
        )

    if status == "input_required":
        return _input_required_hermes_task(task_id, agent_task, elapsed)
    if status in HERMES_COMPLETED_STATUSES or status == "completed":
        return _complete_hermes_task(task_id, agent_task, elapsed)
    if status in HERMES_FAILED_STATUSES or status in {"failed", "timed_out"}:
        return _fail_hermes_task(task_id, agent_task, elapsed)
    if not status:
        return _fail_hermes_task(
            task_id,
            {"status": "unknown", "error": f"AgentService task {agent_task_id} returned no status"},
            elapsed,
        )
    else:
        with _TASKS_LOCK:
            current_task = copy.deepcopy(_TASKS[task_id]["task"])
        return {"done": False, "task": current_task, "event": None}


def _input_required_hermes_task(
    task_id: str,
    agent_task: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    input_request = agent_task.get("input_request") or {}
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["lastHeartbeatAt"] = _timestamp()
        metadata["agentStatus"] = agent_task.get("status")
        metadata["humanRequest"] = input_request
        metadata["humanRequestId"] = input_request.get("request_id")
        task["status"] = {
            "state": "TASK_STATE_INPUT_REQUIRED",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "needs_input",
                    "node_id": metadata["nodeId"],
                    "agent_service_task_id": metadata["agentServiceTaskId"],
                    "human_request": input_request,
                },
            ),
            "timestamp": _timestamp(),
        }
        current_task = copy.deepcopy(task)
    return {
        "done": False,
        "task": current_task,
        "event": {
            "workflow_id": _metadata_string(metadata, "workflow_id"),
            "node_id": metadata["nodeId"],
            "a2a_task_id": task_id,
            "status": "needs_input",
            "a2a_state": "TASK_STATE_INPUT_REQUIRED",
            "a2a_task": current_task,
            "human_request": input_request,
            "human_request_id": input_request.get("request_id"),
        },
    }


def _resume_hermes_task(task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        agent_task_id = metadata["agentServiceTaskId"]
    response = request.get("response", {})
    _request_json(
        _agent_service_api_base(),
        "POST",
        f"/api/v1/agent-tasks/{agent_task_id}/resume",
        {
            "intervention_id": request["intervention_id"],
            "request_id": request.get("human_request_id"),
            "response": response.get("response", response),
            "comment": response.get("comment") or request.get("comment"),
            "responder": response.get("responder") or request.get("responder"),
        },
    )
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        metadata.setdefault("humanResponses", []).append(
            {
                "intervention_id": request["intervention_id"],
                "human_request_id": request.get("human_request_id"),
                "response": response.get("response", response),
                "respondedAt": _timestamp(),
            }
        )
        metadata["humanRequest"] = None
        metadata["humanRequestId"] = None
        task["status"]["state"] = "TASK_STATE_WORKING"
        task["status"]["timestamp"] = _timestamp()
        updated_task = copy.deepcopy(task)
    return {"ok": True, "task": updated_task}

def _complete_hermes_task(
    task_id: str,
    agent_task: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    agent_task_id = _task_identifier(agent_task)
    comments = _safe_agent_service_get(
        f"/tasks/{agent_task_id}/comments",
        {"recent": str(_agent_result_recent_comments())},
    )
    runs = _safe_agent_service_get(f"/tasks/{agent_task_id}/runs")
    messages = None
    if _agent_fetch_run_messages():
        messages = _latest_run_messages(agent_task_id, runs)
    output = str(_latest_comment_raw_text(comments) or "").strip()
    with _TASKS_LOCK:
        metadata_for_validation = copy.deepcopy(_TASKS[task_id]["task"]["metadata"])
    validation_error = _hermes_output_validation_error(metadata_for_validation, output)
    if validation_error:
        return _fail_hermes_task(task_id, validation_error, elapsed)

    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        metadata["agentStatus"] = agent_task.get("status")
        if not record["result_artifact_created"]:
            result = _hermes_result(metadata, agent_task, comments, runs, messages, elapsed)
            task["artifacts"] = [_artifact_from_result(result, metadata)]
            record["result_artifact_created"] = True
        task["status"] = {
            "state": "TASK_STATE_COMPLETED",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "completed",
                    "node_id": metadata["nodeId"],
                    "agent_service_task_id": metadata["agentServiceTaskId"],
                    "agent_status": agent_task.get("status"),
                },
            ),
            "timestamp": _timestamp(),
        }
        completed_task = copy.deepcopy(task)
    _store_idempotent_task(completed_task)
    event = _event_from_a2a_task(
        {
            "workflow_id": _metadata_string(metadata, "workflow_id"),
            "node_id": metadata["nodeId"],
            "a2a_task_id": task_id,
            "status": "completed",
        },
        completed_task,
    )
    return {"done": True, "task": completed_task, "event": event}

def _fail_hermes_task(
    task_id: str,
    agent_task_or_error: dict[str, Any] | str,
    elapsed: float,
) -> dict[str, Any]:
    error = (
        str(agent_task_or_error)
        if isinstance(agent_task_or_error, str)
        else f"AgentService Hermes task ended with status {agent_task_or_error.get('status')}"
    )
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        metadata["error"] = error
        task["status"] = {
            "state": "TASK_STATE_FAILED",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "failed",
                    "node_id": metadata["nodeId"],
                    "agent_service_task_id": metadata.get("agentServiceTaskId"),
                    "error": error,
                },
            ),
            "timestamp": _timestamp(),
        }
        failed_task = copy.deepcopy(task)
    _store_idempotent_task(failed_task)
    event = _event_from_a2a_task(
        {
            "workflow_id": _metadata_string(metadata, "workflow_id"),
            "node_id": metadata["nodeId"],
            "a2a_task_id": task_id,
            "status": "failed",
            "error": error,
        },
        failed_task,
    )
    return {"done": True, "task": failed_task, "event": event}

def _hermes_result(
    metadata: dict[str, Any],
    agent_task: dict[str, Any],
    comments: dict[str, Any] | None,
    runs: dict[str, Any] | None,
    messages: dict[str, Any] | None,
    elapsed: float,
) -> dict[str, Any]:
    output = str(_latest_comment_raw_text(comments) or "").strip()
    # Keep the business output intact in the A2A result artifact. Temporal
    # history/API projections compact it later; downstream Agent nodes should
    # receive the complete upstream result.
    latest_comment = output
    structured_output = _extract_structured_agent_output(output)
    payload = {
        "status": agent_task.get("status"),
        "agent_backend": HERMES_BACKEND,
        "agent_key": metadata.get("agentKey"),
        "requested_agent_key": metadata.get("requestedAgentKey"),
        "context_policy": metadata.get("contextPolicy"),
        "runtime_profile": metadata.get("runtimeProfile"),
        "execution_mode": metadata.get("executionMode"),
        "agent_service_task_id": metadata.get("agentServiceTaskId"),
        "title": agent_task.get("title"),
        "latest_comment": latest_comment,
        "stdout": latest_comment,
        "comments": _compact_comments(comments),
        "runs": _compact_runs(runs),
        "messages": _compact_messages(messages),
        "trace": [
            {
                "type": "agentservice.submit",
                "title": "Submitted Hermes one-shot task to Agent Service",
                "timestamp": _timestamp_from_epoch(metadata.get("startedAt")),
            },
            {
                "type": "agentservice.complete",
                "title": "Agent Service Hermes task returned final output",
                "timestamp": _timestamp(),
                "duration_seconds": elapsed,
            },
        ],
    }
    if structured_output:
        payload["structured_output"] = structured_output
        for key, value in structured_output.items():
            if key not in payload:
                payload[key] = value
    return {
        "node": metadata["nodeId"],
        "operation": metadata["operation"],
        "duration_seconds": elapsed,
        "payload": payload,
    }

def _hermes_output_validation_error(metadata: dict[str, Any], output: str) -> str | None:
    if not output:
        return "Hermes returned an empty final output"
    runtime_error = _looks_like_runtime_failure(output)
    if runtime_error:
        return runtime_error
    if _looks_like_hermes_clarification(output):
        return (
            "Hermes did not execute the node task; it returned a clarification/tool request "
            f"instead. Output preview: {_truncate_text(output, 240)}"
        )
    return None

def _looks_like_runtime_failure(output: str) -> str | None:
    text = output.strip()
    lower = text.lower()
    leading = lower[:1200]
    hard_failure_markers = [
        "api call failed after",
        "http 429",
        "too many requests",
        "rate limit is",
        "request timed out",
        "bad gateway",
        "multica command failed",
        "lightweight hermes execution failed",
        "failed after 3 retries",
    ]
    if any(marker in leading for marker in hard_failure_markers):
        return f"Hermes returned a provider/runtime failure instead of business output: {_truncate_text(text, 240)}"
    if leading.startswith(("traceback", "exception:", "error:", "runtimeerror:")):
        return f"Hermes returned an execution error instead of business output: {_truncate_text(text, 240)}"
    return None

def _looks_like_hermes_clarification(output: str) -> bool:
    text = output.strip()
    lower = text.lower()
    clarification_markers = [
        "请告诉我您需要",
        "请告诉我您想要",
        "请告诉我您",
        "请提供您想要",
        "请提供实际的图片",
        "请提供图像",
        "请提供图片",
        "我没有看到具体的任务内容",
        "没有提供具体的任务内容",
        "没有提供具体任务内容",
        "内容似乎是空的",
        "需要更多信息来理解",
        "provide an image",
        "image url",
        "vision_analyze",
    ]
    if any(marker in text or marker in lower for marker in clarification_markers):
        return True
    question_like = ["您需要我帮助您完成什么", "您想要实现什么目标", "或者直接告诉我任务"]
    return any(marker in text for marker in question_like)

