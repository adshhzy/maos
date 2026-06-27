from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from .bootstrap_patch import start_compact_bootstrap_patch
from .config import DEFAULT_AGENT_PRESETS, Settings
from .hermes import HermesClient, HermesError
from .multica import MulticaClient, MulticaError
from .schemas import (
    AgentTaskCancelRequest,
    AgentServiceCapabilities,
    AgentTaskCreateRequest,
    AgentTaskProjection,
    AgentTaskResumeRequest,
    CommentCreateRequest,
    CommandEnvelope,
    TaskCreateRequest,
    TaskListResponse,
    TaskUpdateRequest,
)

settings = Settings.from_env()
multica = MulticaClient(settings)
hermes = HermesClient(settings)

app = FastAPI(
    title="Agent Service API",
    version="1.0.0",
    description="Stable Agent Service facade backed by Multica.",
)


def _or_502(callable_obj, *args, **kwargs):
    try:
        return callable_obj(*args, **kwargs)
    except (MulticaError, HermesError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, Any]:
    daemon = _or_502(multica.daemon_status)
    return {
        "status": "ok",
        "multica": {
            "status": daemon.get("status"),
            "server_url": daemon.get("server_url"),
            "agents": daemon.get("agents", []),
            "workspaces": daemon.get("workspaces", []),
        },
    }


@app.get("/runtimes", response_model=CommandEnvelope)
def runtimes() -> dict[str, Any]:
    return {"data": _or_502(multica.daemon_status)}


@app.get("/agents", response_model=CommandEnvelope)
def agents(include_archived: bool = False) -> dict[str, Any]:
    return {"data": _or_502(multica.agents, include_archived=include_archived)}


@app.get("/agent-presets", response_model=CommandEnvelope)
def agent_presets() -> dict[str, Any]:
    return {"data": DEFAULT_AGENT_PRESETS}


@app.get("/api/v1/capabilities", response_model=AgentServiceCapabilities, tags=["agent-tasks-v1"])
def agent_service_capabilities_v1() -> dict[str, Any]:
    return _agent_service_capabilities()


@app.post("/api/v1/agent-tasks", response_model=AgentTaskProjection, status_code=201, tags=["agent-tasks-v1"])
def create_agent_task_v1(
    request: AgentTaskCreateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    existing = _find_task_by_idempotency_key(request.idempotency_key)
    if existing:
        return _agent_task_projection(existing, mode="idempotent_reuse")
    legacy_request = _legacy_request_from_agent_task(request)
    task = _create_legacy_task(legacy_request, background_tasks)
    return _agent_task_projection(task, mode="created")


@app.get("/api/v1/agent-tasks/{task_id}", response_model=AgentTaskProjection, tags=["agent-tasks-v1"])
def get_agent_task_v1(task_id: str) -> dict[str, Any]:
    task = _or_502(multica.get_task, task_id)
    return _agent_task_projection(task, mode="poll")


@app.get("/api/v1/agent-tasks/{task_id}/capabilities", tags=["agent-tasks-v1"])
def agent_task_capabilities_v1(task_id: str) -> dict[str, Any]:
    task = _or_502(multica.get_task, task_id)
    return {
        "api_version": "agent-service-v1",
        "task_id": task_id,
        "backend": _agent_backend_from_task(task),
        "capabilities": _agent_task_capabilities(task),
    }


@app.post("/api/v1/agent-tasks/{task_id}/cancel", response_model=AgentTaskProjection, tags=["agent-tasks-v1"])
def cancel_agent_task_v1(
    task_id: str,
    request: AgentTaskCancelRequest | None = None,
) -> dict[str, Any]:
    if request and request.reason:
        try:
            multica.add_comment(
                task_id,
                content=_agent_service_comment(
                    "cancel",
                    {
                        "reason": request.reason,
                        "requested_by": request.requested_by,
                    },
                ),
            )
        except Exception:
            pass
    task = _or_502(multica.set_task_status, task_id, "cancelled")
    return _agent_task_projection(task if isinstance(task, dict) else _or_502(multica.get_task, task_id), mode="cancelled")


@app.post("/api/v1/agent-tasks/{task_id}/resume", response_model=AgentTaskProjection, tags=["agent-tasks-v1"])
def resume_agent_task_v1(
    task_id: str,
    request: AgentTaskResumeRequest,
) -> dict[str, Any]:
    response = dict(request.response or {})
    if request.comment is not None:
        response.setdefault("comment", request.comment)
    comment_payload = {
        "request_id": request.request_id,
        "intervention_id": request.intervention_id,
        "response": response,
        "responder": request.responder,
    }
    _or_502(
        multica.add_comment,
        task_id,
        content=_agent_service_comment("human_response", comment_payload),
    )
    try:
        multica.set_metadata_value(task_id, "last_human_intervention_id", request.intervention_id or "")
        multica.set_metadata_value(task_id, "last_human_request_id", request.request_id or "")
    except Exception:
        pass
    try:
        task = multica.set_task_status(task_id, "in_progress")
    except Exception:
        task = _or_502(multica.get_task, task_id)
    projection = _agent_task_projection(
        task if isinstance(task, dict) else _or_502(multica.get_task, task_id),
        mode="message_appended",
    )
    projection["previous_task_id"] = task_id
    return projection


@app.get("/api/v1/agent-tasks/{task_id}/events", tags=["agent-tasks-v1"])
async def agent_task_events_v1(task_id: str, since: int | None = Query(default=None, ge=0), poll_seconds: float | None = None):
    interval = poll_seconds or settings.poll_seconds

    async def stream():
        last_seen = since or 0
        yield _sse("ready", {"task_id": task_id, "seq": last_seen})
        while True:
            try:
                task = multica.get_task(task_id)
                yield _sse(
                    "heartbeat",
                    {
                        "seq": last_seen,
                        "type": "heartbeat",
                        "task": _agent_task_projection(task, mode="events"),
                    },
                )
                runs_data = multica.runs(task_id)
                active_run_id = _latest_run_id(runs_data)
                if active_run_id:
                    messages = multica.run_messages(active_run_id, issue_id=task_id, since=last_seen)
                    for message in _message_list(messages):
                        seq = _message_sequence(message)
                        if seq is not None:
                            last_seen = max(last_seen, seq)
                        yield _sse("trace", {"seq": seq, "type": "trace", "payload": message})
            except MulticaError as exc:
                yield _sse("error", {"detail": str(exc)})
            await asyncio.sleep(interval)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/v1/agent-tasks/{task_id}/artifacts", tags=["agent-tasks-v1"])
def list_agent_task_artifacts_v1(task_id: str) -> dict[str, Any]:
    return {"artifacts": _agent_task_artifacts(task_id)}


@app.get("/api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}", tags=["agent-tasks-v1"])
def get_agent_task_artifact_v1(task_id: str, artifact_id: str) -> dict[str, Any]:
    for artifact in _agent_task_artifacts(task_id, include_content=True):
        if artifact["artifact_id"] == artifact_id:
            return artifact
    raise HTTPException(status_code=404, detail=f"Artifact not found: {artifact_id}")


@app.get("/api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}/content", tags=["agent-tasks-v1"])
def get_agent_task_artifact_content_v1(task_id: str, artifact_id: str) -> dict[str, Any]:
    artifact = get_agent_task_artifact_v1(task_id, artifact_id)
    return {
        "artifact_id": artifact_id,
        "content": artifact.get("content"),
        "mime_type": artifact.get("mime_type"),
    }


@app.get("/tasks", response_model=TaskListResponse)
def tasks(
    status: str | None = None,
    assignee_id: str | None = None,
    assignee_name: str | None = None,
    priority: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    data = _or_502(
        multica.list_tasks,
        status=status,
        assignee_id=assignee_id,
        assignee_name=assignee_name,
        priority=priority,
        limit=limit,
        offset=offset,
    )
    return {
        "data": data.get("issues", []),
        "total": data.get("total"),
        "has_more": data.get("has_more"),
    }


@app.post("/tasks", response_model=CommandEnvelope, status_code=201)
def create_task(request: TaskCreateRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    return {"data": _create_legacy_task(request, background_tasks)}


def _create_legacy_task(
    request: TaskCreateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    if _should_use_lightweight(request):
        return _create_lightweight_chat_task(request, background_tasks)

    agent_id = _agent_id_from_request(request.agent_key, request.agent_id)
    agent_name = None if agent_id else request.agent_name
    create_status = _initial_multica_status(request.status)
    task = _or_502(
        multica.create_task,
        title=request.title,
        description=request.description,
        agent_id=agent_id,
        agent_name=agent_name,
        priority=request.priority,
        status=create_status,
        project_id=request.project_id,
        parent_id=request.parent_id,
        allow_duplicate=request.allow_duplicate,
    )
    task_id = task.get("id")
    if not task_id:
        if request.metadata:
            task.setdefault("_warnings", []).append("metadata skipped: created task id not found")
        if request.status != create_status:
            task.setdefault("_warnings", []).append("status update skipped: created task id not found")
        return task

    deferred_status = request.status if request.status != create_status else None
    if request.metadata or deferred_status:
        background_tasks.add_task(
            _finish_full_multica_task_setup,
            task_id,
            dict(request.metadata),
            deferred_status,
        )
    if request.metadata:
        task["_metadata_deferred"] = True
    if deferred_status:
        task["_status_deferred"] = deferred_status
        task["_requested_status"] = request.status
    return task


def _initial_multica_status(requested_status: str | None) -> str | None:
    if requested_status == "in_progress":
        return "backlog"
    return requested_status


def _finish_full_multica_task_setup(
    task_id: str,
    metadata: dict[str, str | int | float | bool],
    deferred_status: str | None,
) -> None:
    metadata_with_task = {**metadata, "issue_id": task_id}
    for key, value in metadata_with_task.items():
        try:
            multica.set_metadata_value(task_id, key, value)
        except Exception:
            pass
    if deferred_status == "in_progress":
        start_compact_bootstrap_patch(
            task_id,
            metadata_with_task,
            settings,
            _run_ids_for_task,
        )
    if deferred_status:
        try:
            multica.set_task_status(task_id, deferred_status)
        except Exception as exc:
            try:
                multica.set_metadata_value(task_id, "status_deferred_error", str(exc)[:1000])
            except Exception:
                pass


def _create_lightweight_chat_task(
    request: TaskCreateRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, Any]:
    runtime_agent_key = request.agent_key or "hermes_oneshot"
    preset = DEFAULT_AGENT_PRESETS.get(runtime_agent_key, {})
    runtime_agent_id = request.agent_id or str(preset.get("id") or "")
    task = _or_502(
        multica.create_task,
        title=request.title,
        description=request.description,
        agent_id=None,
        agent_name=None,
        priority=request.priority,
        status="in_progress",
        project_id=request.project_id,
        parent_id=request.parent_id,
        allow_duplicate=request.allow_duplicate,
    )

    task_id = task.get("id")
    task["_lightweight_chat"] = {
        "enabled": True,
        "agent_key": runtime_agent_key,
        "agent_id": runtime_agent_id,
        "runtime": "hermes_oneshot",
        "runs_created": 0,
        "background": True,
    }
    if not task_id:
        task.setdefault("_warnings", []).append(
            "lightweight execution skipped: created task id not found"
        )
        return task

    metadata = {
        **request.metadata,
        "agent_key": runtime_agent_key,
        "agent_id": runtime_agent_id,
        "requested_agent_key": request.agent_key or "",
        "requested_agent_id": request.agent_id or "",
        "requested_agent_name": request.agent_name or "",
        "execution_mode": "lightweight_hermes_oneshot",
        "runtime_profile": "lightweight",
        "runs_created": 0,
        "background": True,
    }
    background_tasks.add_task(
        _complete_lightweight_chat_task,
        task_id,
        _lightweight_chat_prompt(request),
        metadata,
    )
    task["_metadata_deferred"] = True
    return task


def _complete_lightweight_chat_task(
    task_id: str,
    prompt: str,
    metadata: dict[str, str | int | float | bool],
) -> None:
    timeout_seconds = _task_hermes_timeout(metadata)
    workdir = _task_hermes_workdir(metadata)
    try:
        answer = hermes.oneshot(prompt, timeout_seconds=timeout_seconds, workdir=workdir)
        multica.add_comment(task_id, content=answer)
        multica.set_task_status(task_id, "done")
    except Exception as exc:
        detail = f"Lightweight Hermes execution failed: {exc}"
        try:
            multica.add_comment(task_id, content=detail)
        except Exception:
            pass
        try:
            multica.set_task_status(task_id, "blocked")
        except Exception:
            pass
        try:
            multica.set_metadata_value(task_id, "blocked_reason", detail[:1000])
        except Exception:
            pass
    finally:
        for key, value in metadata.items():
            try:
                multica.set_metadata_value(task_id, key, value)
            except Exception:
                pass


def _task_hermes_timeout(metadata: dict[str, str | int | float | bool]) -> float | None:
    for key in ("hermes_timeout_seconds", "timeout_seconds"):
        value = metadata.get(key)
        if value is None:
            continue
        try:
            timeout = float(value)
        except (TypeError, ValueError):
            continue
        if timeout > 0:
            return timeout
    return None


def _task_hermes_workdir(metadata: dict[str, str | int | float | bool]) -> str:
    value = metadata.get("hermes_workdir") or os.environ.get("AGENT_SERVICE_HERMES_WORKDIR")
    if value:
        workdir = Path(str(value))
    else:
        workdir = Path(os.environ.get("MAOS_DATA_DIR", r"D:\dev\MAOS\temporal-data")) / "hermes-lightweight-workdir"
    workdir.mkdir(parents=True, exist_ok=True)
    return str(workdir)


def _should_use_lightweight(request: TaskCreateRequest) -> bool:
    metadata = request.metadata
    execution_mode = _metadata_value(metadata, "execution_mode")
    runtime_profile = _metadata_value(metadata, "runtime_profile", "execution_profile")

    if execution_mode in {"multica", "full_multica", "codex_cli"}:
        return False
    if execution_mode in {"lightweight", "lightweight_hermes_oneshot", "hermes", "hermes_oneshot"}:
        return True
    if runtime_profile in {"full", "full_multica", "codex", "codex_cli"}:
        return False
    if runtime_profile in {"lightweight", "hermes", "hermes_oneshot", "provided_context_only"}:
        return True
    return request.agent_key == "general_chat"


def _metadata_value(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = metadata.get(key)
        if value is not None:
            return str(value).lower().replace("-", "_")
    return ""


def _lightweight_chat_prompt(request: TaskCreateRequest) -> str:
    message = request.description.strip() or request.title.strip()
    requested_role = request.agent_name or request.agent_key or "general_chat"
    return (
        f"Requested role: {requested_role}.\n"
        "Complete only the business task described below. The final answer must be the "
        "business result itself, not a report about executing a workflow, DAG, runtime, "
        "tool, or node.\n"
        "Use only the task description, original task input, and upstream node results provided below. Do not inspect local "
        "files, repositories, prior runs, comments, metadata, external documents, or web "
        "pages. Do not create files or scripts. Do not mention MAOS, DAG, workflow, "
        "runtime, tools, files, validation, or implementation details in the final answer.\n"
        "If the task asks for JSON, output exactly one valid JSON object and nothing else: "
        "no markdown, no preface, no summary, no code fence.\n\n"
        f"{message}"
    )


def _legacy_request_from_agent_task(request: AgentTaskCreateRequest) -> TaskCreateRequest:
    runtime = request.runtime
    metadata = dict(request.metadata)
    if request.idempotency_key:
        metadata["idempotency_key"] = request.idempotency_key
    metadata.update(_scalar_metadata(request.callback.payload if request.callback else {}))
    for key, value in {
        "agent_backend": request.agent.backend,
        "context_policy": runtime.context_policy,
        "runtime_profile": runtime.runtime_profile,
        "execution_mode": runtime.execution_mode,
        "timeout_seconds": runtime.timeout_seconds,
    }.items():
        if value is not None:
            metadata[key] = value
    return TaskCreateRequest(
        title=request.input.title,
        description=request.input.instruction,
        agent_key=request.agent.agent_key,
        agent_id=request.agent.agent_id,
        agent_name=request.agent.agent_name,
        priority=runtime.priority,
        status=runtime.status,
        project_id=runtime.project_id,
        parent_id=runtime.parent_id,
        allow_duplicate=runtime.allow_duplicate,
        metadata=metadata,
    )


def _scalar_metadata(values: dict[str, Any]) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for key, value in values.items():
        if isinstance(value, (str, int, float, bool)):
            result[key] = value
    return result


def _find_task_by_idempotency_key(idempotency_key: str | None) -> dict[str, Any] | None:
    if not idempotency_key:
        return None
    try:
        data = multica.list_tasks(limit=200)
    except Exception:
        return None
    tasks = data.get("issues", data if isinstance(data, list) else [])
    if not isinstance(tasks, list):
        return None
    for task in tasks:
        if not isinstance(task, dict):
            continue
        metadata = task.get("metadata")
        if isinstance(metadata, dict) and metadata.get("idempotency_key") == idempotency_key:
            return task
    return None


def _agent_task_projection(task: dict[str, Any], *, mode: str) -> dict[str, Any]:
    task_id = _task_identifier(task)
    status = _stable_agent_status(task)
    return {
        "api_version": "agent-service-v1",
        "task_id": task_id,
        "external_id": task_id,
        "backend": _agent_backend_from_task(task),
        "status": status,
        "state": _a2a_state_from_status(status),
        "mode": mode,
        "poll_after_seconds": settings.poll_seconds,
        "capabilities": _agent_task_capabilities(task),
        "progress": {
            "phase": status,
            "message": _task_summary(task),
            "percent": None,
        },
        "input_request": _input_request_from_task(task),
        "created_at": task.get("created_at") or task.get("createdAt"),
        "updated_at": task.get("updated_at") or task.get("updatedAt"),
        "raw_status": task.get("status"),
        "title": task.get("title"),
        "metadata": task.get("metadata") if isinstance(task.get("metadata"), dict) else {},
        "links": _agent_task_links(task_id),
    }


def _task_identifier(task: dict[str, Any] | None) -> str | None:
    if not isinstance(task, dict):
        return None
    value = task.get("id") or task.get("task_id") or task.get("issue_id")
    return None if value is None else str(value)


def _stable_agent_status(task: dict[str, Any]) -> str:
    raw_status = str(task.get("status") or "").lower()
    if raw_status in {"backlog", "todo"}:
        return "accepted"
    if raw_status in {"in_progress"}:
        return "working"
    if raw_status in {"waiting_input", "input_required", "needs_input", "human_required"}:
        return "input_required"
    if raw_status in {"done", "in_review"}:
        return "completed"
    if raw_status in {"cancelled", "canceled"}:
        return "cancelled"
    if raw_status in {"blocked", "failed"}:
        return "failed"
    return raw_status or "accepted"


def _a2a_state_from_status(status: str) -> str:
    return {
        "accepted": "TASK_STATE_WORKING",
        "working": "TASK_STATE_WORKING",
        "input_required": "TASK_STATE_INPUT_REQUIRED",
        "completed": "TASK_STATE_COMPLETED",
        "failed": "TASK_STATE_FAILED",
        "cancelled": "TASK_STATE_CANCELED",
        "timed_out": "TASK_STATE_FAILED",
    }.get(status, "TASK_STATE_WORKING")


def _agent_task_capabilities(task: dict[str, Any]) -> dict[str, bool]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    execution_mode = str(metadata.get("execution_mode") or "").lower()
    lightweight = execution_mode in {"lightweight", "lightweight_hermes_oneshot", "hermes_oneshot"}
    return {
        "create": True,
        "poll": True,
        "cancel": True,
        "resume": not lightweight,
        "events": True,
        "artifacts": True,
        "continuation": True,
        "native_resume": False,
        "push_callbacks": False,
    }


def _agent_service_capabilities() -> dict[str, Any]:
    return {
        "api_version": "agent-service-v1",
        "service": "agent-service",
        "operations": {
            "create": True,
            "poll": True,
            "cancel": True,
            "resume": True,
            "events": True,
            "artifacts": True,
            "continuation": True,
            "native_resume": False,
            "push_callbacks": False,
        },
        "statuses": [
            "accepted",
            "working",
            "input_required",
            "completed",
            "failed",
            "cancelled",
            "timed_out",
        ],
        "a2a_states": {
            "accepted": "TASK_STATE_WORKING",
            "working": "TASK_STATE_WORKING",
            "input_required": "TASK_STATE_INPUT_REQUIRED",
            "completed": "TASK_STATE_COMPLETED",
            "failed": "TASK_STATE_FAILED",
            "cancelled": "TASK_STATE_CANCELED",
            "timed_out": "TASK_STATE_FAILED",
        },
        "backends": ["multica", "hermes"],
        "endpoints": {
            "capabilities": "GET /api/v1/capabilities",
            "create": "POST /api/v1/agent-tasks",
            "poll": "GET /api/v1/agent-tasks/{task_id}",
            "task_capabilities": "GET /api/v1/agent-tasks/{task_id}/capabilities",
            "cancel": "POST /api/v1/agent-tasks/{task_id}/cancel",
            "resume": "POST /api/v1/agent-tasks/{task_id}/resume",
            "events": "GET /api/v1/agent-tasks/{task_id}/events",
            "artifacts": "GET /api/v1/agent-tasks/{task_id}/artifacts",
            "artifact": "GET /api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}",
            "artifact_content": "GET /api/v1/agent-tasks/{task_id}/artifacts/{artifact_id}/content",
        },
    }


def _agent_backend_from_task(task: dict[str, Any]) -> str | None:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    value = metadata.get("backend") or metadata.get("agent_backend")
    return None if value is None else str(value)


def _agent_task_links(task_id: str | None) -> dict[str, str]:
    if not task_id:
        return {}
    return {
        "self": f"/api/v1/agent-tasks/{task_id}",
        "capabilities": f"/api/v1/agent-tasks/{task_id}/capabilities",
        "cancel": f"/api/v1/agent-tasks/{task_id}/cancel",
        "resume": f"/api/v1/agent-tasks/{task_id}/resume",
        "events": f"/api/v1/agent-tasks/{task_id}/events",
        "artifacts": f"/api/v1/agent-tasks/{task_id}/artifacts",
    }


def _task_summary(task: dict[str, Any]) -> str:
    for key in ("summary", "description", "body"):
        value = task.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:500]
    return str(task.get("status") or "accepted")


def _input_request_from_task(task: dict[str, Any]) -> dict[str, Any] | None:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    for key in ("input_request", "human_request", "needs_input"):
        value = task.get(key) or metadata.get(key)
        parsed = _json_object(value)
        if parsed:
            return parsed
    return None


def _json_object(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _agent_service_comment(event_type: str, payload: dict[str, Any]) -> str:
    return (
        "MAOS Agent Service Event\n"
        "```json\n"
        + json.dumps({"type": event_type, **payload}, ensure_ascii=False, indent=2)
        + "\n```"
    )


def _agent_task_artifacts(task_id: str, *, include_content: bool = False) -> list[dict[str, Any]]:
    comments = _or_502(multica.list_comments, task_id, recent=20)
    comment_list = _records_from_any(comments, "comments")
    latest_text = ""
    for comment in reversed(comment_list):
        latest_text = _text_from_record(comment)
        if latest_text:
            break
    artifacts: list[dict[str, Any]] = []
    if latest_text:
        artifact = {
            "artifact_id": "final-result",
            "name": "final-result",
            "kind": "text",
            "mime_type": "text/plain; charset=utf-8",
            "size": len(latest_text.encode("utf-8")),
            "summary": latest_text[:240],
            "uri": f"agent-artifact://{task_id}/final-result",
        }
        if include_content:
            artifact["content"] = latest_text
        artifacts.append(artifact)
        structured = _json_object(latest_text)
        if structured:
            structured_artifact = {
                "artifact_id": "structured-output",
                "name": "structured-output",
                "kind": "json",
                "mime_type": "application/json",
                "size": len(json.dumps(structured, ensure_ascii=False).encode("utf-8")),
                "summary": "Structured JSON output extracted from final-result",
                "uri": f"agent-artifact://{task_id}/structured-output",
            }
            if include_content:
                structured_artifact["content"] = structured
            artifacts.append(structured_artifact)
    return artifacts


def _records_from_any(value: Any, preferred_key: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        value = value.get("data", value)
    if isinstance(value, dict):
        for key in (preferred_key, "items", "data", "comments", "messages", "runs"):
            items = value.get(key)
            if isinstance(items, list):
                return [item for item in items if isinstance(item, dict)]
    return []


def _text_from_record(record: dict[str, Any]) -> str:
    for key in ("content", "text", "body", "message", "comment"):
        value = record.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


@app.get("/tasks/{task_id}", response_model=CommandEnvelope)
def get_task(task_id: str) -> dict[str, Any]:
    return {"data": _or_502(multica.get_task, task_id)}


@app.patch("/tasks/{task_id}", response_model=CommandEnvelope)
def update_task(task_id: str, request: TaskUpdateRequest) -> dict[str, Any]:
    data = request.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No update fields supplied")
    if "agent_key" in data:
        data["agent_id"] = _agent_id_from_request(data.pop("agent_key"), data.get("agent_id"))
    return {"data": _or_502(multica.update_task, task_id, **data)}


@app.post("/tasks/{task_id}/status/{status}", response_model=CommandEnvelope)
def set_task_status(task_id: str, status: str) -> dict[str, Any]:
    return {"data": _or_502(multica.set_task_status, task_id, status)}


@app.get("/tasks/{task_id}/comments", response_model=CommandEnvelope)
def list_comments(
    task_id: str,
    recent: int | None = Query(default=None, ge=1, le=100),
    roots_only: bool = False,
    since: str | None = None,
    summary: bool = False,
) -> dict[str, Any]:
    return {
        "data": _or_502(
            multica.list_comments,
            task_id,
            recent=recent,
            roots_only=roots_only,
            since=since,
            summary=summary,
        )
    }


@app.post("/tasks/{task_id}/comments", response_model=CommandEnvelope, status_code=201)
def add_comment(task_id: str, request: CommentCreateRequest) -> dict[str, Any]:
    return {
        "data": _or_502(
            multica.add_comment,
            task_id,
            content=request.content,
            parent_id=request.parent_id,
        )
    }


@app.get("/tasks/{task_id}/runs", response_model=CommandEnvelope)
def runs(task_id: str) -> dict[str, Any]:
    return {"data": _or_502(multica.runs, task_id)}


@app.get("/tasks/{task_id}/metadata", response_model=CommandEnvelope)
def list_metadata(task_id: str) -> dict[str, Any]:
    return {"data": _or_502(multica.list_metadata, task_id)}


@app.get("/runs/{run_id}/messages", response_model=CommandEnvelope)
def run_messages(
    run_id: str,
    issue_id: str | None = None,
    since: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    return {
        "data": _or_502(
            multica.run_messages,
            run_id,
            issue_id=issue_id,
            since=since,
        )
    }


@app.get("/tasks/{task_id}/events")
async def task_events(task_id: str, poll_seconds: float | None = None):
    interval = poll_seconds or settings.poll_seconds

    async def stream():
        last_seen = 0
        yield _sse("ready", {"task_id": task_id})
        while True:
            try:
                runs_data = multica.runs(task_id)
                active_run_id = _latest_run_id(runs_data)
                if active_run_id:
                    messages = multica.run_messages(active_run_id, issue_id=task_id, since=last_seen)
                    for message in _message_list(messages):
                        seq = _message_sequence(message)
                        if seq is not None:
                            last_seen = max(last_seen, seq)
                        yield _sse("message", message)
                else:
                    yield _sse("heartbeat", {"task_id": task_id, "run_id": None})
            except MulticaError as exc:
                yield _sse("error", {"detail": str(exc)})
            await asyncio.sleep(interval)

    return StreamingResponse(stream(), media_type="text/event-stream")


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _latest_run_id(runs_data: Any) -> str | None:
    candidates = runs_data.get("runs") if isinstance(runs_data, dict) else runs_data
    if not isinstance(candidates, list) or not candidates:
        return None
    latest = max(
        candidates,
        key=lambda item: item.get("created_at") or item.get("started_at") or "",
    )
    return latest.get("id") or latest.get("task_id")


def _run_ids_for_task(task_id: str) -> list[str]:
    runs_data = multica.runs(task_id)
    candidates = runs_data.get("runs") if isinstance(runs_data, dict) else runs_data
    if not isinstance(candidates, list):
        return []
    ids: list[str] = []
    for item in candidates:
        if not isinstance(item, dict):
            continue
        run_id = item.get("id") or item.get("task_id")
        if run_id:
            ids.append(str(run_id))
    return ids


def _message_list(messages: Any) -> list[dict[str, Any]]:
    if isinstance(messages, list):
        return messages
    if isinstance(messages, dict):
        for key in ("messages", "data", "items"):
            value = messages.get(key)
            if isinstance(value, list):
                return value
    return []


def _message_sequence(message: dict[str, Any]) -> int | None:
    for key in ("sequence", "seq", "sequence_number"):
        value = message.get(key)
        if isinstance(value, int):
            return value
    return None


def _agent_id_from_request(agent_key: str | None, agent_id: str | None) -> str | None:
    if not agent_key:
        return agent_id
    preset = DEFAULT_AGENT_PRESETS.get(agent_key)
    if not preset:
        known = ", ".join(sorted(DEFAULT_AGENT_PRESETS))
        raise HTTPException(
            status_code=400,
            detail=f"Unknown agent_key '{agent_key}'. Known keys: {known}",
        )
    return preset["id"]
