import copy
from typing import Any

from maos_runtime.a2a_constants import SIMULATOR_BACKEND
from maos_runtime.a2a_provider_base import AgentRuntimeProvider
from maos_runtime.a2a_provider_registry import (
    list_provider_backends,
    register_provider,
)
from maos_runtime.a2a_task_store import (
    TASKS as _TASKS,
    TASKS_LOCK as _TASKS_LOCK,
    request_idempotency_key as _request_idempotency_key,
    store_idempotent_task as _store_idempotent_task,
    task_for_idempotency_key as _task_for_idempotency_key,
)
from maos_runtime.a2a.providers import ClaudeCliProvider, CodexCliProvider, HermesOneshotProvider, MulticaProvider, SimulatorProvider
from maos_runtime.a2a.agent_service_client import _timestamp
from maos_runtime.a2a.runtime_config_helpers import _provider_for_backend, _provider_for_node
from maos_runtime.a2a.messages import (
    _agent_message,
    _artifact_from_result,
    _dependency_results_from_artifacts,
    _event_from_a2a_task,
    _first_data_part,
)


def register_agent_provider(
    provider: AgentRuntimeProvider,
    *,
    aliases: list[str] | tuple[str, ...] = (),
    replace: bool = False,
) -> None:
    """注册一个 Agent runtime provider。

    外部扩展只需要实现 AgentRuntimeProvider，然后调用这个函数注册 backend。
    """

    register_provider(provider, aliases=aliases, replace=replace)


def registered_agent_backends() -> list[str]:
    return list_provider_backends()


def provider_capabilities(backend: str | None = None) -> dict[str, Any]:
    if backend:
        return _provider_for_backend(backend).capabilities()
    return {
        "api_version": "agent-provider-v1",
        "providers": {
            provider_backend: _provider_for_backend(provider_backend).capabilities()
            for provider_backend in registered_agent_backends()
        },
    }


def _register_default_providers() -> None:
    register_agent_provider(SimulatorProvider(), replace=True)
    register_agent_provider(MulticaProvider(), replace=True)
    register_agent_provider(HermesOneshotProvider(), replace=True)
    register_agent_provider(CodexCliProvider(), replace=True)
    register_agent_provider(ClaudeCliProvider(), replace=True)


_register_default_providers()


def get_agent_card(node: dict[str, Any]) -> dict[str, Any]:
    return _provider_for_node(node).agent_card(node)


def send_message(request: dict[str, Any]) -> dict[str, Any]:
    # Temporal activity retry 时可能再次进入这里；先用 idempotency key 复用已创建任务。
    message = request["message"]
    payload = _first_data_part(message)
    node = payload["node"]
    existing_task = _task_for_idempotency_key(_request_idempotency_key(request))
    if existing_task:
        return {"task": existing_task}
    return _provider_for_node(node).create(request)


def poll_task(request: dict[str, Any]) -> dict[str, Any]:
    # workflow 每次被 Temporal 拉起时只做一次短轮询，未完成则继续 durable sleep。
    task_id = request["id"]
    task_hint = request.get("task")
    with _TASKS_LOCK:
        if task_id not in _TASKS and task_hint:
            _TASKS[task_id] = {"task": copy.deepcopy(task_hint), "result_artifact_created": False}
        record = _TASKS[task_id]
        task = record["task"]
    metadata = task.get("metadata", {})
    backend = metadata.get("backend", SIMULATOR_BACKEND)
    return _provider_for_backend(backend).poll(task_id, request)


def cancel_task(request: dict[str, Any]) -> dict[str, Any]:
    task_id, task = _task_and_record_from_request(request)
    backend = task.get("metadata", {}).get("backend", SIMULATOR_BACKEND)
    return _provider_for_backend(backend).cancel(task_id, request)


def resume_task_with_human_response(request: dict[str, Any]) -> dict[str, Any]:
    task_id, task = _task_and_record_from_request(request)
    backend = task.get("metadata", {}).get("backend", SIMULATOR_BACKEND)
    return _provider_for_backend(backend).resume(task_id, request)


def task_events(request: dict[str, Any]) -> dict[str, Any]:
    task_id, task = _task_and_record_from_request(request)
    backend = task.get("metadata", {}).get("backend", SIMULATOR_BACKEND)
    return _provider_for_backend(backend).events(task_id, request)


def task_artifacts(request: dict[str, Any]) -> dict[str, Any]:
    task_id, task = _task_and_record_from_request(request)
    backend = task.get("metadata", {}).get("backend", SIMULATOR_BACKEND)
    return _provider_for_backend(backend).artifacts(task_id, request)


def _task_and_record_from_request(request: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    task_id = request["id"]
    task_hint = request.get("task")
    with _TASKS_LOCK:
        if task_id not in _TASKS and task_hint:
            _TASKS[task_id] = {
                "task": copy.deepcopy(task_hint),
                "result_artifact_created": False,
            }
        record = _TASKS[task_id]
        return task_id, record["task"]


def get_task(request: dict[str, Any]) -> dict[str, Any]:
    return poll_task(request)["task"]


def complete_task_from_agent_callback(callback: dict[str, Any]) -> dict[str, Any]:
    if callback.get("a2a_task"):
        return _event_from_a2a_task(callback, callback["a2a_task"])

    task_id = callback["a2a_task_id"]
    if callback.get("idempotency_key"):
        _task_for_idempotency_key(str(callback["idempotency_key"]))
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        job = callback.get("job", {})
        metadata["heartbeatCount"] += 1
        metadata["elapsedSeconds"] = job.get("elapsed_seconds", 0)
        metadata["lastHeartbeatAt"] = _timestamp()

        callback_status = str(callback["status"]).lower()
        if callback_status == "completed":
            if not record["result_artifact_created"]:
                result = callback["result"]
                task["artifacts"] = [_artifact_from_result(result, metadata)]
                task["status"] = {
                    "state": "TASK_STATE_COMPLETED",
                    "message": _agent_message(
                        task_id,
                        task["contextId"],
                        {
                            "status": "completed",
                            "node_id": metadata["nodeId"],
                            "simulator_job_id": metadata.get("simulatorJobId"),
                        },
                    ),
                    "timestamp": _timestamp(),
                }
                metadata["finishedAt"] = job.get("finished_at")
                record["result_artifact_created"] = True
        elif callback_status == "failed":
            task["status"] = {
                "state": "TASK_STATE_FAILED",
                "message": _agent_message(
                    task_id,
                    task["contextId"],
                    {
                        "status": "failed",
                        "node_id": metadata["nodeId"],
                        "error": callback.get("error"),
                    },
                ),
                "timestamp": _timestamp(),
            }
            metadata["error"] = callback.get("error")
            metadata["finishedAt"] = job.get("finished_at")
        elif callback_status in {
            "needs_input",
            "input_required",
            "waiting_human",
            "human_required",
        }:
            human_request = (
                callback.get("human_request")
                or callback.get("input_request")
                or job.get("current_human_request")
                or {}
            )
            request_id = str(
                human_request.get("request_id")
                or human_request.get("id")
                or callback.get("human_request_id")
                or f"{task_id}:human:{metadata.get('humanRequestCount', 0) + 1}"
            )
            human_request = {**human_request, "request_id": request_id}
            metadata["humanRequest"] = human_request
            metadata["humanRequestId"] = request_id
            metadata["humanRequestCount"] = int(metadata.get("humanRequestCount", 0)) + 1
            task["status"] = {
                "state": "TASK_STATE_INPUT_REQUIRED",
                "message": _agent_message(
                    task_id,
                    task["contextId"],
                    {
                        "status": "needs_input",
                        "node_id": metadata["nodeId"],
                        "human_request": human_request,
                    },
                ),
                "timestamp": _timestamp(),
            }
        else:
            task["status"]["message"] = _agent_message(
                task_id,
                task["contextId"],
                {
                    "status": "working",
                    "node_id": metadata["nodeId"],
                    "simulator_job_id": metadata.get("simulatorJobId"),
                    "elapsed_seconds": metadata["elapsedSeconds"],
                    "planned_duration_seconds": metadata.get("plannedDurationSeconds"),
                },
            )

        completed_task = copy.deepcopy(task)

    _store_idempotent_task(completed_task)
    return _event_from_a2a_task(callback, completed_task)


def extract_result_from_task(task: dict[str, Any]) -> dict[str, Any]:
    if task["status"]["state"] != "TASK_STATE_COMPLETED":
        raise ValueError(f"A2A task is not completed: {task['status']['state']}")
    for artifact in task.get("artifacts", []):
        if artifact.get("name") == "dag-node-result":
            return _first_data_part({"parts": artifact["parts"]})
    raise ValueError(f"A2A task {task['id']} completed without result artifact")












































































































































































