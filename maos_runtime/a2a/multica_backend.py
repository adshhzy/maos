"""Multica-backed A2A provider implementation."""

import copy
import time
import uuid
from typing import Any

from maos_runtime.a2a_constants import MULTICA_BACKEND, MULTICA_COMPLETED_STATUSES, MULTICA_FAILED_STATUSES
from maos_runtime.a2a.messages import _agent_message, _artifact_from_result, _dependency_results_from_artifacts, _event_from_a2a_task, _first_data_part
from maos_runtime.a2a.runtime_config_helpers import (
    _is_lightweight_multica_node,
    _multica_context_policy,
    _multica_dispatch_agent_key,
    _multica_execution_mode,
    _multica_requested_agent_name,
    _multica_runtime_profile,
    _node_agent_config,
    _node_result_text_limit,
    _stable_runtime_id,
)
from maos_runtime.a2a.prompt_builders import _multica_description, _multica_metadata, _multica_title
from maos_runtime.a2a.agent_service_client import (
    _compact_comments,
    _compact_messages,
    _compact_runs,
    _latest_comment_raw_text,
    _latest_run_messages,
    _records_from_response,
    _safe_agent_service_get,
    _task_identifier,
    _timestamp,
)
from maos_runtime.a2a.text_parsing import _extract_structured_agent_output, _metadata_string, _truncate_text
from maos_runtime.a2a_task_store import TASKS as _TASKS, TASKS_LOCK as _TASKS_LOCK, request_idempotency_key as _request_idempotency_key, store_idempotent_task as _store_idempotent_task, store_task as _store_task
from maos_runtime.http_json_client import request_json as _request_json
from maos_runtime.runtime_config import (
    agent_fetch_run_messages as _agent_fetch_run_messages,
    agent_poll_seconds as _agent_poll_seconds,
    agent_result_recent_comments as _agent_result_recent_comments,
    agent_service_api_base as _agent_service_api_base,
)


def _send_multica_message(request: dict[str, Any]) -> dict[str, Any]:
    from maos_runtime.a2a.runtime import get_agent_card

    message = request["message"]
    request_metadata = request.get("metadata", {})
    payload = _first_data_part(message)
    node = payload["node"]
    if isinstance(payload.get("control_flow"), dict):
        node = {**node, "control_flow": payload["control_flow"]}
    agent = _node_agent_config(node)
    idempotency_key = _request_idempotency_key(request)
    task_id = _stable_runtime_id("a2a-task", node["id"], idempotency_key)
    context_id = message.get("contextId") or f"context-{uuid.uuid4()}"
    workflow_id = request_metadata["workflow_id"]
    dependency_results = _dependency_results_from_artifacts(
        payload.get("dependency_artifacts", []),
        expected_workflow_id=workflow_id,
    )
    context_policy = _multica_context_policy(node)
    runtime_profile = _multica_runtime_profile(node)
    execution_mode = _multica_execution_mode(node)
    requested_agent_key = agent.get("agent_key") or node.get("agent_key")
    dispatch_agent_key = _multica_dispatch_agent_key(node)
    result_text_limit = _node_result_text_limit(node)

    create_payload = {
        "title": _multica_title(node, workflow_id),
        "description": _multica_description(
            node,
            dependency_results,
            payload.get("graph_input", {}),
            workflow_id,
            task_id,
        ),
        "agent_key": dispatch_agent_key,
        "agent_id": (
            None
            if _is_lightweight_multica_node(node)
            else agent.get("agent_id") or node.get("agent_id")
        ),
        "agent_name": _multica_requested_agent_name(node),
        "priority": agent.get("priority") or node.get("priority") or "medium",
        "status": agent.get("status") or "todo",
        "project_id": agent.get("project_id") or node.get("project_id"),
        "parent_id": agent.get("parent_id") or node.get("parent_id"),
        "allow_duplicate": bool(agent.get("allow_duplicate", True)),
        "metadata": _multica_metadata(
            node,
            workflow_id,
            task_id,
            context_id,
            dependency_results,
            payload.get("graph_input", {}),
            context_policy,
            runtime_profile,
            execution_mode,
            dispatch_agent_key,
            requested_agent_key,
            idempotency_key,
        ),
    }
    create_payload["metadata"]["result_text_limit"] = result_text_limit
    create_payload = {key: value for key, value in create_payload.items() if value is not None}
    multica_task = _find_multica_task_by_idempotency_key(idempotency_key)
    if multica_task is None:
        multica_task = _request_json(
            _agent_service_api_base(),
            "POST",
            "/api/v1/agent-tasks",
            _agent_task_create_payload(
                create_payload,
                backend=MULTICA_BACKEND,
                idempotency_key=idempotency_key,
                context={
                    "graph_input": payload.get("graph_input", {}),
                    "dependencies": dependency_results,
                },
            ),
        )
    multica_task_id = _task_identifier(multica_task)
    if not multica_task_id:
        raise RuntimeError(f"AgentService task response did not contain an id: {multica_task!r}")

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
                    "agent_service_task_id": multica_task_id,
                    "agent_status": multica_task.get("status"),
                },
            ),
            "timestamp": now,
        },
        "artifacts": [],
        "history": [message],
        "metadata": {
            "agentCard": get_agent_card(node),
            "backend": MULTICA_BACKEND,
            "idempotencyKey": idempotency_key,
            "completionMode": "polling",
            "nodeId": node["id"],
            "workflow_id": workflow_id,
            "operation": node.get("operation", "agent_task"),
            "referenceTaskIds": message.get("referenceTaskIds", []),
            "agentServiceTaskId": multica_task_id,
            "agentServiceBase": _agent_service_api_base(),
            "agentKey": create_payload.get("agent_key"),
            "requestedAgentKey": requested_agent_key,
            "agentId": create_payload.get("agent_id"),
            "agentName": create_payload.get("agent_name"),
            "contextPolicy": context_policy,
            "runtimeProfile": runtime_profile,
            "executionMode": execution_mode,
            "agentStatus": multica_task.get("status"),
            "pollSeconds": poll_seconds,
            "plannedDurationSeconds": None,
            "startedAt": time.time(),
            "lastHeartbeatAt": now,
            "heartbeatCount": 0,
            "resultTextLimit": result_text_limit,
            "callbackMode": "temporal-durable-polling-until-agentservice-exposes-push-events",
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
                "runMessages": "GET /runs/{run_id}/messages",
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
        },
        "metadata": metadata,
    }


def _poll_multica_task(task_id: str) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
    multica_task_id = metadata["agentServiceTaskId"]
    task_response = _request_json(
        _agent_service_api_base(),
        "GET",
        f"/api/v1/agent-tasks/{multica_task_id}",
    )
    multica_task = task_response.get("data", task_response)
    status = str(multica_task.get("status") or "").lower()
    now = _timestamp()
    elapsed = round(time.time() - float(metadata.get("startedAt", time.time())), 2)

    with _TASKS_LOCK:
        metadata["heartbeatCount"] += 1
        metadata["elapsedSeconds"] = elapsed
        metadata["lastHeartbeatAt"] = now
        metadata["agentStatus"] = status or multica_task.get("status")
        task["status"]["timestamp"] = now
        task["status"]["message"] = _agent_message(
            task_id,
            task["contextId"],
            {
                "status": "working",
                "node_id": metadata["nodeId"],
                "agent_service_task_id": multica_task_id,
                "agent_status": status,
                "elapsed_seconds": elapsed,
            },
        )

    if status == "input_required":
        return _input_required_multica_task(task_id, multica_task, elapsed)
    if status in MULTICA_COMPLETED_STATUSES or status == "completed":
        return _complete_multica_task(task_id, multica_task, elapsed)
    if status in MULTICA_FAILED_STATUSES or status in {"failed", "timed_out"}:
        return _fail_multica_task(task_id, multica_task, elapsed)

    with _TASKS_LOCK:
        current_task = copy.deepcopy(_TASKS[task_id]["task"])
    return {"done": False, "task": current_task, "event": None}


def _input_required_multica_task(
    task_id: str,
    multica_task: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    input_request = multica_task.get("input_request") or {}
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["lastHeartbeatAt"] = _timestamp()
        metadata["agentStatus"] = multica_task.get("status")
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


def _resume_multica_task(task_id: str, request: dict[str, Any]) -> dict[str, Any]:
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

def _complete_multica_task(
    task_id: str,
    multica_task: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    comments = _safe_agent_service_get(
        f"/tasks/{_task_identifier(multica_task)}/comments",
        {"recent": str(_agent_result_recent_comments())},
    )
    runs = _safe_agent_service_get(f"/tasks/{_task_identifier(multica_task)}/runs")
    messages = None
    if _agent_fetch_run_messages():
        messages = _latest_run_messages(_task_identifier(multica_task), runs)
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        metadata["agentStatus"] = multica_task.get("status")
        if not record["result_artifact_created"]:
            result = _multica_result(metadata, multica_task, comments, runs, messages, elapsed)
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
                    "agent_status": multica_task.get("status"),
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

def _fail_multica_task(
    task_id: str,
    multica_task: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    error = f"Multica task ended with status {multica_task.get('status')}"
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        metadata["error"] = error
        metadata["agentStatus"] = multica_task.get("status")
        task["status"] = {
            "state": "TASK_STATE_FAILED",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "failed",
                    "node_id": metadata["nodeId"],
                    "agent_service_task_id": metadata["agentServiceTaskId"],
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

def _multica_result(
    metadata: dict[str, Any],
    multica_task: dict[str, Any],
    comments: dict[str, Any] | None,
    runs: dict[str, Any] | None,
    messages: dict[str, Any] | None,
    elapsed: float,
) -> dict[str, Any]:
    latest_comment_raw = _latest_comment_raw_text(comments)
    # Keep the business output intact in the A2A result artifact. Temporal
    # history/API projections compact it later; downstream Agent nodes should
    # receive the complete upstream result.
    latest_comment = latest_comment_raw
    structured_output = _extract_structured_agent_output(latest_comment_raw)
    payload = {
        "status": multica_task.get("status"),
        "agent_backend": MULTICA_BACKEND,
        "agent_key": metadata.get("agentKey"),
        "requested_agent_key": metadata.get("requestedAgentKey"),
        "context_policy": metadata.get("contextPolicy"),
        "runtime_profile": metadata.get("runtimeProfile"),
        "execution_mode": metadata.get("executionMode"),
        "agent_id": metadata.get("agentId"),
        "agent_name": metadata.get("agentName"),
        "agent_service_task_id": metadata["agentServiceTaskId"],
        "title": multica_task.get("title"),
        "latest_comment": latest_comment,
        "comments": _compact_comments(comments),
        "runs": _compact_runs(runs),
        "messages": _compact_messages(messages),
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

def _find_multica_task_by_idempotency_key(idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    response = _safe_agent_service_get("/tasks", {"limit": "200"})
    tasks = _records_from_response(response)
    for task in tasks:
        metadata = task.get("metadata") if isinstance(task, dict) else None
        if isinstance(metadata, dict) and metadata.get("idempotency_key") == idempotency_key:
            return task
    return None
