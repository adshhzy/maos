import random
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from string import Template
from typing import Any
from urllib.request import Request, urlopen


_LOCK = threading.Lock()
_CONDITION = threading.Condition(_LOCK)
_EXECUTOR = ThreadPoolExecutor(max_workers=32, thread_name_prefix="dag-simulator")
_JOBS: dict[str, dict[str, Any]] = {}


def start_simulated_job(
    node: dict[str, Any],
    dependency_results: dict[str, Any],
    graph_input: dict[str, Any],
    callback: dict[str, Any] | None = None,
    job_id: str | None = None,
) -> dict[str, Any]:
    simulation = node.get("simulate", {})
    min_seconds = float(simulation.get("min_seconds", 5))
    max_seconds = float(simulation.get("max_seconds", 120))
    duration_seconds = round(random.uniform(min_seconds, max_seconds), 2)
    job_id = job_id or f"sim-{node['id']}-{uuid.uuid4()}"
    started_at = time.time()

    with _LOCK:
        existing_job = _JOBS.get(job_id)
        if existing_job:
            return {
                "job_id": existing_job["job_id"],
                "planned_duration_seconds": existing_job["planned_duration_seconds"],
                "started_at": existing_job["started_at"],
            }
        _JOBS[job_id] = {
            "job_id": job_id,
            "node_id": node["id"],
            "status": "running",
            "planned_duration_seconds": duration_seconds,
            "started_at": started_at,
            "finished_at": None,
            "result": None,
            "error": None,
            "callback_url": None if callback is None else callback.get("url"),
            "callback_sent_at": None,
            "callback_error": None,
            "current_human_request": None,
            "human_responses": [],
        }

    _EXECUTOR.submit(
        _run_job,
        job_id,
        node,
        dependency_results,
        graph_input,
        duration_seconds,
        callback,
    )
    return {
        "job_id": job_id,
        "planned_duration_seconds": duration_seconds,
        "started_at": started_at,
    }


def get_job_status(job_id: str) -> dict[str, Any]:
    with _LOCK:
        job = _JOBS[job_id].copy()
    job["elapsed_seconds"] = round(time.time() - job["started_at"], 2)
    return job


def submit_human_response(
    job_id: str,
    intervention_id: str,
    response: dict[str, Any],
) -> dict[str, Any]:
    with _CONDITION:
        job = _JOBS[job_id]
        current_request = job.get("current_human_request") or {}
        response_record = {
            "intervention_id": intervention_id,
            "human_request_id": response.get("human_request_id")
            or current_request.get("request_id"),
            "response": response.get("response", response),
            "decision": response.get("decision"),
            "comment": response.get("comment"),
            "responder": response.get("responder"),
            "responded_at": time.time(),
        }
        job["human_responses"].append(response_record)
        job["current_human_request"] = None
        job["status"] = "running"
        _CONDITION.notify_all()
        return job.copy()


def _run_job(
    job_id: str,
    node: dict[str, Any],
    dependency_results: dict[str, Any],
    graph_input: dict[str, Any],
    duration_seconds: float,
    callback: dict[str, Any] | None,
) -> None:
    callback_event: dict[str, Any] | None = None
    try:
        spent_seconds = _run_human_checkpoints(
            job_id,
            node,
            duration_seconds,
            callback,
        )
        if spent_seconds < duration_seconds:
            time.sleep(max(0, duration_seconds - spent_seconds))
        with _LOCK:
            human_responses = list(_JOBS[job_id].get("human_responses") or [])
        payload = run_operation(
            operation=node.get("operation", "merge"),
            params=node.get("params", {}),
            dependency_results=dependency_results,
            graph_input=graph_input,
            human_responses=human_responses,
        )
        result = {
            "node": node["id"],
            "operation": node.get("operation", "merge"),
            "duration_seconds": duration_seconds,
            "payload": payload,
        }
        with _LOCK:
            _JOBS[job_id]["status"] = "completed"
            _JOBS[job_id]["finished_at"] = time.time()
            _JOBS[job_id]["result"] = result
        callback_event = _callback_event(job_id, "completed", result=result)
    except Exception as exc:
        with _LOCK:
            _JOBS[job_id]["status"] = "failed"
            _JOBS[job_id]["finished_at"] = time.time()
            _JOBS[job_id]["error"] = str(exc)
        callback_event = _callback_event(job_id, "failed", error=str(exc))

    if callback and callback_event:
        _send_callback(job_id, callback, callback_event)


def _run_human_checkpoints(
    job_id: str,
    node: dict[str, Any],
    duration_seconds: float,
    callback: dict[str, Any] | None,
) -> float:
    requests = node.get("simulate", {}).get("human_interventions") or []
    if not isinstance(requests, list) or not requests:
        return 0

    spent_seconds = 0.0
    default_wait = min(3.0, max(0.1, duration_seconds / (len(requests) + 1)))
    for index, request_spec in enumerate(requests, start=1):
        wait_seconds = float(request_spec.get("after_seconds", default_wait))
        wait_seconds = max(0.0, min(wait_seconds, max(0.0, duration_seconds - spent_seconds)))
        if wait_seconds:
            time.sleep(wait_seconds)
            spent_seconds += wait_seconds
        human_request = _human_request_from_spec(node, request_spec, index)
        with _CONDITION:
            _JOBS[job_id]["status"] = "waiting_human"
            _JOBS[job_id]["current_human_request"] = human_request
        if callback:
            _send_callback(
                job_id,
                callback,
                _callback_event(
                    job_id,
                    "needs_input",
                    human_request=human_request,
                ),
            )
        _wait_for_human_response(job_id, human_request["request_id"])
    return spent_seconds


def _human_request_from_spec(
    node: dict[str, Any],
    request_spec: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    request_id = str(
        request_spec.get("request_id")
        or request_spec.get("id")
        or f"{node['id']}-input-{index}"
    )
    return {
        "request_id": request_id,
        "prompt": request_spec.get("prompt")
        or request_spec.get("description")
        or f"Please provide input for {node['id']}.",
        "schema": request_spec.get("schema") or request_spec.get("response_schema") or {},
        "assignee": request_spec.get("assignee"),
        "assignee_role": request_spec.get("assignee_role"),
        "choices": request_spec.get("choices") or request_spec.get("decisions") or [],
        "required": bool(request_spec.get("required", True)),
        "source": "simulator",
    }


def _wait_for_human_response(job_id: str, request_id: str) -> None:
    with _CONDITION:
        while True:
            job = _JOBS[job_id]
            for response in job.get("human_responses") or []:
                if response.get("human_request_id") == request_id:
                    return
            _CONDITION.wait(timeout=1)


def run_operation(
    operation: str,
    params: dict[str, Any],
    dependency_results: dict[str, Any],
    graph_input: dict[str, Any],
    human_responses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context = _build_context(params, dependency_results, graph_input, human_responses)

    if operation == "emit":
        return _resolve_value(params, context)

    if operation == "merge":
        payload = {"params": _resolve_value(params, context)}
        payload["dependencies"] = {
            node_id: result["payload"]
            for node_id, result in dependency_results.items()
        }
        payload["human_responses"] = human_responses or []
        return payload

    if operation == "template":
        return {
            key: _template(str(value), context)
            for key, value in params.get("fields", {}).items()
        }

    if operation == "count":
        values = _resolve_value(params["values"], context)
        return {
            "count": len(values),
            "values": values,
        }

    if operation == "percentage_from_count":
        count = int(_resolve_value(params["count"], context))
        multiplier = int(params.get("multiplier", 1))
        cap = int(params.get("cap", count * multiplier))
        return {"percent": min(cap, count * multiplier)}

    if operation == "status":
        return {
            "status": params.get("status", "ok"),
            "details": _resolve_value(params.get("details", {}), context),
        }

    if operation == "join":
        fields = params.get("fields", {})
        return {
            key: _resolve_value(value, context)
            for key, value in fields.items()
        }

    raise ValueError(f"Unsupported DAG node operation: {operation}")


def _build_context(
    params: dict[str, Any],
    dependency_results: dict[str, Any],
    graph_input: dict[str, Any],
    human_responses: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    context = {
        "params": params,
        "input": graph_input,
        "deps": {},
        "human_responses": human_responses or [],
    }
    for node_id, result in dependency_results.items():
        context["deps"][node_id] = _dependency_payload_for_context(result.get("payload", {}))
    return context


def _dependency_payload_for_context(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return payload
    context_payload = dict(payload)
    summary = context_payload.get("summary")
    omitted = set(context_payload.get("_omitted_dependency_fields") or [])
    if summary is not None:
        if "latest_comment" in omitted and "latest_comment" not in context_payload:
            context_payload["latest_comment"] = summary
        if "stdout" in omitted and "stdout" not in context_payload:
            context_payload["stdout"] = summary
    return context_payload


def _resolve_value(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return _lookup_path(value[1:], context)
    if isinstance(value, dict):
        return {
            key: _resolve_value(nested_value, context)
            for key, nested_value in value.items()
        }
    if isinstance(value, list):
        return [_resolve_value(item, context) for item in value]
    return value


def _lookup_path(path: str, context: dict[str, Any]) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, dict):
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(f"Cannot resolve {path!r} at {part!r}")
    return current


def _template(template_value: str, context: dict[str, Any]) -> str:
    flat_context = _flatten_context(context)
    return Template(template_value).safe_substitute(flat_context)


def _flatten_context(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        flattened: dict[str, Any] = {}
        for key, nested_value in value.items():
            nested_prefix = f"{prefix}_{key}" if prefix else key
            flattened.update(_flatten_context(nested_value, nested_prefix))
        return flattened
    return {prefix: value}


def _callback_event(
    job_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    human_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job = get_job_status(job_id)
    event = {
        "status": status,
        "job": job,
        "result": result,
        "error": error,
    }
    if human_request:
        event["human_request"] = human_request
        event["human_request_id"] = human_request.get("request_id")
    return event


def _send_callback(
    job_id: str,
    callback: dict[str, Any],
    event: dict[str, Any],
) -> None:
    payload = dict(callback.get("payload", {}))
    payload.update(event)
    body = json.dumps(payload).encode("utf-8")
    last_error = None
    for _ in range(3):
        try:
            request = Request(
                callback["url"],
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request, timeout=15) as response:
                response.read()
            with _LOCK:
                _JOBS[job_id]["callback_sent_at"] = time.time()
                _JOBS[job_id]["callback_error"] = None
            return
        except Exception as exc:
            last_error = str(exc)
            time.sleep(1)
    with _LOCK:
        _JOBS[job_id]["callback_error"] = last_error
