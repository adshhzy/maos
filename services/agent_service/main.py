from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

from .bootstrap_patch import start_compact_bootstrap_patch
from .config import DEFAULT_AGENT_PRESETS, Settings
from .hermes import HermesClient, HermesError
from .multica import MulticaClient, MulticaError
from .schemas import (
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
    version="0.1.0",
    description="Stable Agent Service facade backed by Multica.",
)


def _or_502(callable_obj, *args, **kwargs):
    try:
        return callable_obj(*args, **kwargs)
    except (MulticaError, HermesError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, Any]:
    return readyz()


@app.get("/livez")
def livez() -> dict[str, Any]:
    return {"ok": True, "service": "agent-service"}


@app.get("/readyz")
def readyz() -> dict[str, Any]:
    daemon = _or_502(multica.daemon_status)
    return {
        "ok": True,
        "service": "agent-service",
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
    if _should_use_lightweight(request):
        return {"data": _create_lightweight_chat_task(request, background_tasks)}

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
        return {"data": task}

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
    return {"data": task}


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
    preset = DEFAULT_AGENT_PRESETS["general_chat"]
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
        "agent_key": "general_chat",
        "agent_id": preset["id"],
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
        "agent_key": "general_chat",
        "agent_id": preset["id"],
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
    try:
        answer = hermes.oneshot(prompt)
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
        "You are completing a MAOS DAG node through the lightweight Agent "
        f"runtime. Requested role: {requested_role}.\n"
        "Use only the task description and A2A context payload provided below. "
        "Do not inspect repositories, workspace files, prior runs, comments, "
        "metadata, external documents, or web pages unless the task explicitly "
        "asks for that. Answer concisely with the node result only.\n\n"
        f"{message}"
    )


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
