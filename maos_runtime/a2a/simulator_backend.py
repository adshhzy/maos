"""Simulator-backed A2A provider implementation."""

import copy
import uuid
from typing import Any

from maos_runtime.a2a_constants import SIMULATOR_BACKEND
from maos_runtime.a2a.messages import _agent_message, _dependency_results_from_artifacts, _first_data_part
from maos_runtime.a2a.runtime_config_helpers import _stable_runtime_id
from maos_runtime.a2a.agent_service_client import _timestamp
from maos_runtime.a2a_task_store import TASKS as _TASKS, TASKS_LOCK as _TASKS_LOCK, request_idempotency_key as _request_idempotency_key, store_task as _store_task
from maos_runtime.http_json_client import request_json as _request_json
from maos_runtime.runtime_config import sandbox_api_base as _sandbox_api_base, simulator_api_base as _simulator_api_base


def _send_simulator_message(request: dict[str, Any]) -> dict[str, Any]:
    from maos_runtime.a2a.runtime import get_agent_card

    message = request["message"]
    request_metadata = request.get("metadata", {})
    payload = _first_data_part(message)
    node = payload["node"]
    idempotency_key = _request_idempotency_key(request)
    task_id = _stable_runtime_id("a2a-task", node["id"], idempotency_key)
    context_id = message.get("contextId") or f"context-{uuid.uuid4()}"
    workflow_id = request_metadata["workflow_id"]
    dependency_results = _dependency_results_from_artifacts(
        payload.get("dependency_artifacts", []),
        expected_workflow_id=workflow_id,
    )
    simulator_job = _request_json(
        _simulator_api_base(),
        "POST",
        "/api/simulator/jobs",
        {
            "job_id": _stable_runtime_id("sim", node["id"], idempotency_key),
            "idempotency_key": idempotency_key,
            "node": node,
            "dependency_results": dependency_results,
            "graph_input": payload.get("graph_input", {}),
            "callback": {
                "url": f"{_sandbox_api_base()}/api/agent-callbacks",
                "payload": {
                    "workflow_id": workflow_id,
                    "node_id": node["id"],
                    "a2a_task_id": task_id,
                    "context_id": context_id,
                    "idempotency_key": idempotency_key,
                },
            },
        },
    )["job"]
    now = _timestamp()
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
                    "simulator_job_id": simulator_job["job_id"],
                    "planned_duration_seconds": simulator_job["planned_duration_seconds"],
                },
            ),
            "timestamp": now,
        },
        "artifacts": [],
        "history": [message],
        "metadata": {
            "agentCard": get_agent_card(node),
            "backend": SIMULATOR_BACKEND,
            "idempotencyKey": idempotency_key,
            "completionMode": "callback",
            "nodeId": node["id"],
            "workflow_id": workflow_id,
            "operation": node.get("operation", "merge"),
            "referenceTaskIds": message.get("referenceTaskIds", []),
            "simulatorJobId": simulator_job["job_id"],
            "plannedDurationSeconds": simulator_job["planned_duration_seconds"],
            "startedAt": simulator_job["started_at"],
            "lastHeartbeatAt": now,
            "heartbeatCount": 0,
            "callbackMode": "agent-service-calls-sandbox-then-sandbox-signals-workflow",
            "transport": {
                "simulatorApiBase": _simulator_api_base(),
                "createJob": "POST /api/simulator/jobs",
                "callback": "POST /api/agent-callbacks",
            },
        },
    }
    _store_task(task)
    return {"task": copy.deepcopy(task)}

def _poll_simulator_task(task_id: str, workflow_id: str | None = None) -> dict[str, Any]:
    from maos_runtime.a2a.runtime import complete_task_from_agent_callback

    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
    metadata = task["metadata"]
    job = _request_json(
        _simulator_api_base(),
        "GET",
        f"/api/simulator/jobs/{metadata['simulatorJobId']}",
    )
    status = job["status"]
    callback_status = "needs_input" if status == "waiting_human" else status
    callback = {
        "workflow_id": workflow_id,
        "node_id": metadata["nodeId"],
        "a2a_task_id": task_id,
        "context_id": task["contextId"],
        "status": callback_status,
        "job": job,
        "result": job.get("result"),
        "error": job.get("error"),
    }
    if callback_status == "needs_input":
        callback["human_request"] = job.get("current_human_request") or {}
    return complete_task_from_agent_callback(callback)["a2a_task"]


def _resume_simulator_task(task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
    response = request.get("response", {})
    _request_json(
        _simulator_api_base(),
        "POST",
        f"/api/simulator/jobs/{metadata['simulatorJobId']}/human-responses",
        {
            "intervention_id": request["intervention_id"],
            "human_request_id": request.get("human_request_id"),
            "responder": response.get("responder") or request.get("responder"),
            "decision": response.get("decision") or request.get("decision"),
            "comment": response.get("comment") or request.get("comment"),
            "response": response.get("response", response),
        },
    )
    with _TASKS_LOCK:
        task = _TASKS[task_id]["task"]
        metadata = task["metadata"]
        responses = metadata.setdefault("humanResponses", [])
        responses.append(
            {
                "intervention_id": request["intervention_id"],
                "human_request_id": request.get("human_request_id"),
                "responder": response.get("responder") or request.get("responder"),
                "decision": response.get("decision") or request.get("decision"),
                "comment": response.get("comment") or request.get("comment"),
                "response": response.get("response", response),
                "respondedAt": _timestamp(),
            }
        )
        metadata["humanRequest"] = None
        metadata["humanRequestId"] = None
        metadata["lastHeartbeatAt"] = _timestamp()
        task["status"] = {
            "state": "TASK_STATE_WORKING",
            "message": _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "working",
                    "node_id": metadata["nodeId"],
                    "simulator_job_id": metadata.get("simulatorJobId"),
                    "resumed_from_human_intervention": request["intervention_id"],
                },
            ),
            "timestamp": _timestamp(),
        }
        updated_task = copy.deepcopy(task)
    return {"ok": True, "task": updated_task}
