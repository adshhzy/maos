"""Deterministic evaluator A2A provider implementation."""

from __future__ import annotations

import copy
import time
import uuid
from typing import Any

from maos_runtime.a2a.agent_service_client import _timestamp
from maos_runtime.a2a.messages import (
    _agent_message,
    _artifact_from_result,
    _event_from_a2a_task,
    _first_data_part,
)
from maos_runtime.a2a.runtime_config_helpers import _stable_runtime_id
from maos_runtime.a2a_constants import EVALUATOR_BACKEND
from maos_runtime.a2a_task_store import (
    TASKS as _TASKS,
    TASKS_LOCK as _TASKS_LOCK,
    request_idempotency_key as _request_idempotency_key,
    store_idempotent_task as _store_idempotent_task,
    store_task as _store_task,
)
from maos_runtime.evaluation import run_evaluation


def _send_evaluator_message(request: dict[str, Any]) -> dict[str, Any]:
    from maos_runtime.a2a.runtime import get_agent_card

    message = request["message"]
    request_metadata = request.get("metadata", {})
    payload = _first_data_part(message)
    node = payload["node"]
    idempotency_key = _request_idempotency_key(request)
    task_id = _stable_runtime_id("a2a-task", node["id"], idempotency_key)
    context_id = message.get("contextId") or f"context-{uuid.uuid4()}"
    workflow_id = request_metadata["workflow_id"]
    config = _evaluation_config(node, payload.get("graph_input", {}))
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
                    "runtime": EVALUATOR_BACKEND,
                    "benchmark_id": config.get("benchmark_id", "async_ttl_cache"),
                },
            ),
            "timestamp": now,
        },
        "artifacts": [],
        "history": [message],
        "metadata": {
            "agentCard": get_agent_card(node),
            "backend": EVALUATOR_BACKEND,
            "idempotencyKey": idempotency_key,
            "completionMode": "polling",
            "nodeId": node["id"],
            "workflow_id": workflow_id,
            "operation": node.get("operation", "evaluate"),
            "referenceTaskIds": message.get("referenceTaskIds", []),
            "evaluationTaskId": task_id,
            "evaluationConfig": config,
            "benchmarkId": config.get("benchmark_id", "async_ttl_cache"),
            "startedAt": time.time(),
            "lastHeartbeatAt": now,
            "heartbeatCount": 0,
            "callbackMode": "temporal-durable-polling-deterministic-evaluator",
            "transport": {
                "runtime": "local deterministic evaluator",
                "benchmark_id": config.get("benchmark_id", "async_ttl_cache"),
            },
        },
    }
    _store_task(task)
    return {"task": copy.deepcopy(task)}


def _poll_evaluator_task(task_id: str) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        if task["status"]["state"] in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"}:
            return {"done": True, "task": copy.deepcopy(task), "event": None}
        metadata["heartbeatCount"] += 1
        metadata["lastHeartbeatAt"] = _timestamp()

    started_at = float(metadata.get("startedAt", time.time()))
    elapsed = round(time.time() - started_at, 3)
    try:
        report = run_evaluation(metadata.get("evaluationConfig", {}))
    except Exception as exc:
        return _fail_evaluator_task(task_id, str(exc), elapsed)
    return _complete_evaluator_task(task_id, report, elapsed)


def _evaluation_config(node: dict[str, Any], graph_input: dict[str, Any]) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if isinstance(graph_input, dict):
        config.update(graph_input)
    params = node.get("params")
    if isinstance(params, dict):
        config.update(params)
    agent = node.get("agent")
    if isinstance(agent, dict):
        evaluator = agent.get("evaluator")
        if isinstance(evaluator, dict):
            config.update(evaluator)
    config.setdefault("benchmark_id", "async_ttl_cache")
    return config


def _complete_evaluator_task(
    task_id: str,
    report: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        metadata["elapsedSeconds"] = elapsed
        metadata["finishedAt"] = _timestamp()
        if not record["result_artifact_created"]:
            result = _evaluator_result(metadata, report, elapsed)
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
                    "runtime": EVALUATOR_BACKEND,
                    "winner": report.get("winner"),
                },
            ),
            "timestamp": _timestamp(),
        }
        completed_task = copy.deepcopy(task)
    _store_idempotent_task(completed_task)
    event = _event_from_a2a_task(
        {
            "workflow_id": metadata.get("workflow_id"),
            "node_id": metadata["nodeId"],
            "a2a_task_id": task_id,
            "status": "completed",
        },
        completed_task,
    )
    return {"done": True, "task": completed_task, "event": event}


def _fail_evaluator_task(task_id: str, error: str, elapsed: float) -> dict[str, Any]:
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
                    "runtime": EVALUATOR_BACKEND,
                    "error": error,
                },
            ),
            "timestamp": _timestamp(),
        }
        failed_task = copy.deepcopy(task)
    _store_idempotent_task(failed_task)
    event = _event_from_a2a_task(
        {
            "workflow_id": metadata.get("workflow_id"),
            "node_id": metadata["nodeId"],
            "a2a_task_id": task_id,
            "status": "failed",
            "error": error,
        },
        failed_task,
    )
    return {"done": True, "task": failed_task, "event": event}


def _evaluator_result(
    metadata: dict[str, Any],
    report: dict[str, Any],
    elapsed: float,
) -> dict[str, Any]:
    single = report.get("single", {})
    multi = report.get("multi", {})
    latest = report.get("markdown", "")
    comparison_sources = report.get("comparison_sources", {})
    return {
        "status": "completed",
        "agent_backend": EVALUATOR_BACKEND,
        "evaluation_task_id": metadata.get("evaluationTaskId"),
        "benchmark_id": report.get("benchmark_id"),
        "comparison_sources": comparison_sources,
        "winner": report.get("winner"),
        "decision": report.get("winner"),
        "score_delta": report.get("score_delta"),
        "single_score": single.get("score"),
        "multi_score": multi.get("score"),
        "single": single,
        "multi": multi,
        "report": report,
        "latest_comment": latest,
        "stdout": latest,
        "duration_seconds": elapsed,
        "payload": {
            "benchmark_id": report.get("benchmark_id"),
            "comparison_sources": comparison_sources,
            "winner": report.get("winner"),
            "single_score": single.get("score"),
            "multi_score": multi.get("score"),
        },
        "trace": [
            {
                "type": "evaluator.run",
                "title": "Ran deterministic benchmark",
                "description": "Executed local extraction, compile checks, hidden tests, and scoring.",
                "duration_seconds": elapsed,
            }
        ],
    }
