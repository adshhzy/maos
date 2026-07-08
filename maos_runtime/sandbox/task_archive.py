"""Local task archive used as a fallback for Temporal visibility retention."""

from __future__ import annotations

import json
import os
import re
import time
import copy
from pathlib import Path
from typing import Any

from maos_runtime.graph.control_flow import normalize_graph
from maos_runtime.persistence import (
    list_workflow_runs,
    load_workflow_task_projection,
    load_workflow_task_snapshot,
    persist_workflow_task_snapshot,
)
from maos_runtime.sandbox.api_projection import _task_list_item_for_api
from maos_runtime.sandbox.constants import COMPLETED_WORKFLOW_DISPLAY_LIMIT

_PROVIDER_RECOVERY_CACHE_TTL_SECONDS = 5.0
_provider_recovery_cache_at = 0.0
_provider_recovery_cache: list[dict[str, Any]] | None = None
_provider_recovery_cache_key: str | None = None


def archive_task(task: dict[str, Any]) -> None:
    task_id = task.get("task_id") or task.get("workflow_id")
    if not task_id:
        return
    _persist_execution_snapshot(task, source="task_archive")
    directory = _archive_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{_safe_id(str(task_id))}.json"
    payload = dict(task)
    payload["archived_at"] = time.time()
    temp_path = path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, path)


def archive_workflow_result(
    *,
    workflow_id: str,
    result: dict[str, Any],
    submitted_at: Any = None,
    updated_at: Any = None,
) -> dict[str, Any]:
    """Persist the final workflow result before Temporal visibility expires."""

    state = result.get("state") if isinstance(result.get("state"), dict) else {}
    status = str(result.get("status") or state.get("workflow_status") or "completed")
    task = {
        "task_id": workflow_id,
        "workflow_id": workflow_id,
        "graph_id": result.get("graph_id") or state.get("graph_id") or workflow_id,
        "graph_name": result.get("graph_name") or state.get("graph_name") or workflow_id,
        "graph_type": result.get("graph_type") or state.get("graph_type"),
        "submitted_at": submitted_at,
        "updated_at": updated_at,
        "status": status,
        "state": state,
        "result": result,
        "error": result.get("error"),
        "archived_by": "workflow_completion_activity",
    }
    archive_task({key: value for key, value in task.items() if value is not None})
    return {
        "ok": True,
        "workflow_id": workflow_id,
        "status": status,
        "archive_path": str(_archive_dir() / f"{_safe_id(workflow_id)}.json"),
    }


def load_archived_task(task_id: str) -> dict[str, Any] | None:
    path = _archive_dir() / f"{_safe_id(task_id)}.json"
    if not path.is_file():
        recovered = recovered_task_from_provider_task_store(task_id)
        return recovered or load_workflow_task_projection(task_id) or load_workflow_task_snapshot(task_id)
    try:
        archived = json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None
    return _reconcile_archived_task_with_provider_store(archived)


def load_archived_tasks(limit: int | None = None) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in _archive_dir().glob("*.json"):
        try:
            archived = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        rows.append(_reconcile_archived_task_with_provider_store(archived))
    rows.extend(recovered_tasks_from_provider_task_store(limit=limit))
    rows.extend(_execution_store_task_snapshots(limit=limit))
    dedup: dict[str, dict[str, Any]] = {}
    for task in rows:
        task_id = task.get("task_id") or task.get("workflow_id")
        if not task_id:
            continue
        current = dedup.get(task_id)
        if current is None or _archive_list_rank(task) > _archive_list_rank(current):
            dedup[str(task_id)] = task
    tasks = sorted(dedup.values(), key=_task_sort_key, reverse=True)
    return tasks[:limit] if limit else tasks


def _execution_store_task_snapshots(limit: int | None = None) -> list[dict[str, Any]]:
    snapshots: list[dict[str, Any]] = []
    for row in list_workflow_runs(limit=limit or 1000):
        workflow_id = str(row.get("workflow_id") or "")
        task = load_workflow_task_projection(workflow_id) or load_workflow_task_snapshot(workflow_id)
        if task:
            snapshots.append(task)
    return snapshots


def _persist_execution_snapshot(task: dict[str, Any], *, source: str) -> None:
    try:
        persist_workflow_task_snapshot(task, source=source)
    except Exception:
        return


def prefer_archived_task(task: dict[str, Any], archived: dict[str, Any] | None) -> dict[str, Any]:
    """Use a full local archive when Temporal can only provide a broken shell."""

    if not archived:
        return task
    task_status = str(task.get("status") or "")
    archived_status = str(archived.get("status") or "")
    if _is_closed_status(task_status) and not _is_closed_status(archived_status):
        return _archived_task_with_live_closed_status(archived, task)
    if not _task_has_graph(archived):
        return task
    if not _task_has_graph(task):
        return archived
    if _is_temporal_replay_error(task.get("error")):
        return archived
    if task.get("status") == "running" and archived.get("status") in {
        "completed",
        "failed",
        "cancelled",
        "terminated",
        "timed_out",
    }:
        return archived
    return task


def _archived_task_with_live_closed_status(
    archived: dict[str, Any],
    live_task: dict[str, Any],
) -> dict[str, Any]:
    status = str(live_task.get("status") or archived.get("status") or "failed")
    merged = dict(archived)
    merged["status"] = status
    if live_task.get("error"):
        merged["error"] = live_task.get("error")
    state = dict(merged.get("state") or {})
    state["workflow_status"] = status
    if status in {"cancelled", "terminated"}:
        state["nodes"] = [
            _node_with_cancelled_status(node)
            if isinstance(node, dict)
            else node
            for node in state.get("nodes", [])
        ]
    merged["state"] = state
    result = merged.get("result")
    if isinstance(result, dict):
        result = dict(result)
        result["status"] = status
        result_state = result.get("state")
        if isinstance(result_state, dict):
            result_state = dict(result_state)
            result_state["workflow_status"] = status
            result["state"] = result_state
        merged["result"] = result
    return merged


def _reconcile_archived_task_with_provider_store(task: dict[str, Any]) -> dict[str, Any]:
    """Refresh stale archived node states from the durable provider task store."""

    task_id = str(task.get("task_id") or task.get("workflow_id") or "")
    if not task_id:
        return task
    recovered = recovered_task_from_provider_task_store(task_id)
    if not recovered or recovered is task:
        return task
    recovered_nodes = {
        str(node.get("id")): node
        for node in (recovered.get("state") or {}).get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    }
    if not recovered_nodes:
        return task

    merged = dict(task)
    state = dict(merged.get("state") or {})
    nodes = state.get("nodes") if isinstance(state.get("nodes"), list) else []
    state["nodes"] = [
        _merge_archived_node_with_recovered(node, recovered_nodes)
        if isinstance(node, dict)
        else node
        for node in nodes
    ]
    state_status = _workflow_status_from_nodes(
        [node for node in state["nodes"] if isinstance(node, dict)]
    )
    recovered_status = str(recovered.get("status") or "")
    if _is_closed_status(recovered_status) or state_status != "running":
        state["workflow_status"] = recovered_status if _is_closed_status(recovered_status) else state_status
        merged["status"] = state["workflow_status"]
    else:
        state["workflow_status"] = state.get("workflow_status") or state_status

    merged["state"] = state
    result = merged.get("result")
    if isinstance(result, dict):
        result = dict(result)
        result_state = result.get("state")
        if isinstance(result_state, dict):
            result_state = dict(result_state)
            result_state["nodes"] = [
                _merge_archived_node_with_recovered(node, recovered_nodes)
                if isinstance(node, dict)
                else node
                for node in (result_state.get("nodes") or [])
            ]
            result_state["workflow_status"] = state.get("workflow_status")
            result["state"] = result_state
        result["status"] = merged.get("status")
        merged["result"] = result
    return merged


def _merge_archived_node_with_recovered(
    node: dict[str, Any],
    recovered_nodes: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    recovered = recovered_nodes.get(str(node.get("id")))
    if not recovered:
        return node
    current_status = str(node.get("status") or "")
    recovered_status = str(recovered.get("status") or "")
    if not _provider_node_is_newer_or_more_final(current_status, recovered_status):
        return node
    merged = dict(node)
    for key in (
        "status",
        "summary",
        "a2a_state",
        "a2a_task_id",
        "agent_service_task_id",
        "hermes_job_id",
        "codex_task_id",
        "claude_task_id",
        "elapsed_seconds",
        "heartbeat_count",
        "artifact_count",
        "finished_at",
        "duration_seconds",
    ):
        if key in recovered:
            merged[key] = recovered[key]
    if recovered.get("instances"):
        merged["instances"] = recovered["instances"]
        merged["current_instance_id"] = recovered["instances"][-1].get("id")
        merged["visits"] = len(recovered["instances"])
    return merged


def _provider_node_is_newer_or_more_final(current_status: str, recovered_status: str) -> bool:
    if recovered_status in {"completed", "failed", "cancelled"} and current_status in {
        "",
        "pending",
        "running",
        "suspended",
    }:
        return True
    return False


def _node_with_cancelled_status(node: dict[str, Any]) -> dict[str, Any]:
    status = node.get("status")
    if status not in {"running", "suspended"}:
        return node
    updated = dict(node)
    updated["status"] = "cancelled"
    updated["summary"] = "Cancelled because the parent workflow stopped."
    return updated


def _is_closed_status(status: str) -> bool:
    return status in {"completed", "failed", "cancelled", "terminated", "timed_out"}


def recovered_task_from_provider_task_store(task_id: str) -> dict[str, Any] | None:
    for task in recovered_tasks_from_provider_task_store(limit=None):
        if task.get("task_id") == task_id or task.get("workflow_id") == task_id:
            return task
    return None


def recovered_tasks_from_provider_task_store(limit: int | None = None) -> list[dict[str, Any]]:
    cached = _cached_recovered_provider_tasks()
    if cached is not None:
        return cached[:limit] if limit else cached

    registry = _load_provider_task_records()
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in registry.values():
        workflow_id = item.get("workflow_id")
        if not workflow_id:
            continue
        grouped.setdefault(str(workflow_id), []).append(item)
    tasks = [_task_from_provider_task_group(workflow_id, rows) for workflow_id, rows in grouped.items()]
    tasks = [task for task in tasks if task is not None]
    tasks.sort(key=_task_sort_key, reverse=True)
    _store_recovered_provider_tasks(tasks)
    return copy.deepcopy(tasks[:limit] if limit else tasks)


def _cached_recovered_provider_tasks() -> list[dict[str, Any]] | None:
    global _provider_recovery_cache_at, _provider_recovery_cache, _provider_recovery_cache_key
    if _provider_recovery_cache is None:
        return None
    if _provider_recovery_cache_key != _provider_recovery_source_key():
        _provider_recovery_cache = None
        _provider_recovery_cache_key = None
        return None
    if time.time() - _provider_recovery_cache_at > _PROVIDER_RECOVERY_CACHE_TTL_SECONDS:
        _provider_recovery_cache = None
        _provider_recovery_cache_key = None
        return None
    return copy.deepcopy(_provider_recovery_cache)


def _store_recovered_provider_tasks(tasks: list[dict[str, Any]]) -> None:
    global _provider_recovery_cache_at, _provider_recovery_cache, _provider_recovery_cache_key
    _provider_recovery_cache_at = time.time()
    _provider_recovery_cache_key = _provider_recovery_source_key()
    _provider_recovery_cache = copy.deepcopy(tasks)


def _provider_recovery_source_key() -> str:
    return "|".join(
        str(os.environ.get(key) or "")
        for key in (
            "MAOS_DATA_DIR",
            "A2A_PROVIDER_TASK_DB_FILE",
            "MAOS_RUNTIME_DB_FILE",
        )
    )


def _task_from_provider_task_group(workflow_id: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not rows:
        return None
    rows = sorted(rows, key=lambda item: item.get("created_at") or "")
    graph_id = _graph_id_from_workflow_id(workflow_id)
    graph_template = _load_graph_template(graph_id)
    recovered_nodes = _recovered_nodes_from_rows(rows)
    if graph_template:
        nodes = _nodes_from_graph_template(graph_template, recovered_nodes)
        edges = graph_template.get("edges", [])
        levels = graph_template.get("levels", [])
        graph_name = graph_template.get("name") or _graph_name_from_workflow_id(workflow_id)
        graph_type = graph_template.get("graph_type", "recovered")
        recovered_from = "provider_task_store+graph_template"
    else:
        nodes = list(recovered_nodes.values())
        edges = []
        levels = [[node["id"]] for node in nodes]
        graph_name = _graph_name_from_workflow_id(workflow_id)
        graph_type = "recovered"
        recovered_from = "provider_task_store"
    if not nodes:
        return None
    status = _workflow_status_from_nodes(nodes)
    submitted_at = _parse_time(rows[0].get("created_at"))
    updated_at = max(_parse_time(item.get("updated_at")) for item in rows)
    state = {
        "graph_id": graph_id,
        "graph_name": graph_name,
        "graph_type": graph_type,
        "workflow_status": status,
        "nodes": nodes,
        "edges": edges,
        "levels": levels,
        "human_interventions": [],
        "pending_human_interventions": [],
        "recovered_from": recovered_from,
    }
    return {
        "task_id": workflow_id,
        "workflow_id": workflow_id,
        "graph_id": state["graph_id"],
        "graph_name": state["graph_name"],
        "submitted_at": submitted_at,
        "updated_at": updated_at,
        "status": status,
        "state": state,
        "result": None,
        "error": None,
        "recovered": True,
    }


def _recovered_nodes_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    for item in rows:
        node_id = item.get("node_id")
        if not node_id:
            continue
        backend = item.get("backend")
        status = _node_status_from_a2a(item.get("status"))
        a2a_task_id = item.get("a2a_task_id")
        task = item.get("task") if isinstance(item.get("task"), dict) else {}
        artifact_count = len(task.get("artifacts") or []) if isinstance(task, dict) else 0
        started_at = item.get("created_at")
        finished_at = item.get("finished_at") or item.get("updated_at")
        duration_seconds = _duration_seconds(started_at, finished_at)
        node = {
            "id": node_id,
            "label": node_id,
            "status": status,
            "backend": backend,
            "summary": f"Recovered from provider task store; status={item.get('status')}",
            "a2a_state": item.get("status"),
            "a2a_task_id": a2a_task_id,
            "agent_service_task_id": None,
            "hermes_job_id": None,
            "codex_task_id": a2a_task_id if backend == "codex" else None,
            "claude_task_id": a2a_task_id if backend in {"claude", "claude-huawei"} else None,
            "elapsed_seconds": duration_seconds or 0,
            "duration_seconds": duration_seconds,
            "heartbeat_count": 0,
            "artifact_count": artifact_count,
            "finished_at": finished_at if status in {"completed", "failed", "cancelled"} else None,
            "instances": [
                {
                    "id": f"{node_id}#1",
                    "node_id": node_id,
                    "visit": 1,
                    "status": status,
                    "backend": backend,
                    "a2a_task_id": a2a_task_id,
                    "claude_task_id": a2a_task_id if backend in {"claude", "claude-huawei"} else None,
                    "codex_task_id": a2a_task_id if backend == "codex" else None,
                    "started_at": started_at,
                    "finished_at": finished_at if status in {"completed", "failed", "cancelled"} else None,
                    "elapsed_seconds": duration_seconds or 0,
                    "artifact_count": artifact_count,
                    "summary": f"Recovered provider task {a2a_task_id}",
                }
            ],
        }
        previous = nodes.get(node_id)
        current_time = _parse_time(item.get("updated_at")) or _parse_time(item.get("finished_at")) or 0.0
        previous_time = (
            _parse_time(previous["instances"][-1].get("finished_at"))
            if previous and previous.get("instances")
            else None
        )
        if previous_time is None and previous:
            previous_time = _parse_time(previous.get("finished_at")) or 0.0
        if previous is None or current_time >= previous_time:
            nodes[str(node_id)] = node
    return nodes


def _nodes_from_graph_template(
    graph: dict[str, Any],
    recovered_nodes: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for index, spec in enumerate(graph.get("nodes", [])):
        node_id = spec.get("id")
        if not node_id:
            continue
        recovered = recovered_nodes.get(str(node_id), {})
        agent = spec.get("agent") if isinstance(spec.get("agent"), dict) else {}
        backend = recovered.get("backend") or agent.get("backend") or spec.get("backend") or spec.get("runtime") or "simulator"
        node = {
            "id": node_id,
            "label": spec.get("label") or node_id,
            "type": spec.get("type"),
            "operation": spec.get("operation", "agent_task"),
            "status": recovered.get("status", "pending"),
            "backend": backend,
            "deps": spec.get("deps", []),
            "summary": recovered.get("summary", "Recovered graph template node; no provider invocation recorded yet."),
            "a2a_state": recovered.get("a2a_state"),
            "a2a_task_id": recovered.get("a2a_task_id"),
            "agent_service_task_id": recovered.get("agent_service_task_id"),
            "hermes_job_id": recovered.get("hermes_job_id"),
            "codex_task_id": recovered.get("codex_task_id"),
            "claude_task_id": recovered.get("claude_task_id"),
            "elapsed_seconds": recovered.get("elapsed_seconds", 0),
            "duration_seconds": recovered.get("duration_seconds"),
            "heartbeat_count": recovered.get("heartbeat_count", 0),
            "artifact_count": recovered.get("artifact_count"),
            "finished_at": recovered.get("finished_at"),
            "instances": recovered.get("instances", []),
            "current_instance_id": recovered.get("instances", [{}])[-1].get("id") if recovered.get("instances") else None,
            "max_visits": spec.get("max_visits") or spec.get("max_attempts"),
            "visits": len(recovered.get("instances", [])),
        }
        nodes.append({key: value for key, value in node.items() if value is not None})
    return nodes


def merge_visible_tasks(live_tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visible_live: list[dict[str, Any]] = []
    for task in live_tasks:
        task_id = str(task.get("task_id") or task.get("workflow_id") or "")
        archived = load_archived_task(task_id) if task_id else None
        selected = prefer_archived_task(task, archived)
        visible_live.append(selected)
        if selected is task and _task_has_graph(task):
            archive_task(task)
    seen = {str(task.get("task_id") or task.get("workflow_id")) for task in visible_live}
    archived_limit = int(os.environ.get("SANDBOX_ARCHIVED_TASK_DISPLAY_LIMIT", str(COMPLETED_WORKFLOW_DISPLAY_LIMIT)))
    archived = [
        task
        for task in load_archived_tasks(limit=archived_limit * 3)
        if str(task.get("task_id") or task.get("workflow_id")) not in seen
    ]
    completed = 0
    merged = list(visible_live)
    for task in archived:
        status = task.get("status")
        if status == "completed":
            if completed >= archived_limit:
                continue
            completed += 1
        merged.append(task)
    merged.sort(key=_task_sort_key, reverse=True)
    return [_task_list_item_for_api(task) for task in merged]


def _archive_dir() -> Path:
    base = Path(os.environ.get("MAOS_DATA_DIR", "D:/dev/MAOS/temporal-data"))
    return base / "task-archive"


def _load_provider_task_records() -> dict[str, Any]:
    try:
        from maos_runtime.a2a_task_store import load_invocation_records

        data = load_invocation_records()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _load_graph_template(graph_id: str) -> dict[str, Any] | None:
    for path in _graph_search_paths():
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if graph.get("id") != graph_id:
            continue
        try:
            return normalize_graph(graph)
        except Exception:
            return graph
    return None


def _graph_search_paths() -> list[Path]:
    roots = []
    configured = os.environ.get("MAOS_GRAPH_TEMPLATE_DIRS")
    if configured:
        roots.extend(Path(item) for item in configured.split(os.pathsep) if item)
    project_root = Path(__file__).resolve().parents[2]
    roots.extend([project_root / "example_cmp", project_root / "examples"])
    paths: list[Path] = []
    for root in roots:
        if root.is_dir():
            paths.extend(sorted(root.glob("*.json")))
    return paths


def _safe_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "task"


def _task_has_graph(task: dict[str, Any]) -> bool:
    state = task.get("state")
    if isinstance(state, dict) and (state.get("nodes") or state.get("edges")):
        return True
    result = task.get("result")
    if isinstance(result, dict):
        result_state = result.get("state")
        if isinstance(result_state, dict) and (result_state.get("nodes") or result_state.get("edges")):
            return True
        if result.get("results") or result.get("instance_results"):
            return True
    return False


def _is_temporal_replay_error(error: Any) -> bool:
    text = str(error or "").lower()
    return any(
        marker in text
        for marker in (
            "nondeterminism",
            "tmprl1100",
            "workflow task in failed state",
            "replay",
        )
    )


def _node_status_from_a2a(status: Any) -> str:
    value = str(status or "").upper()
    if value == "TASK_STATE_COMPLETED":
        return "completed"
    if value == "TASK_STATE_FAILED":
        return "failed"
    if value in {"TASK_STATE_CANCELED", "TASK_STATE_CANCELLED"}:
        return "cancelled"
    return "suspended"


def _workflow_status_from_nodes(nodes: list[dict[str, Any]]) -> str:
    statuses = {node.get("status") for node in nodes}
    if "failed" in statuses:
        return "failed"
    if statuses and statuses <= {"completed"}:
        return "completed"
    return "running"


def _parse_time(value: Any) -> float | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except Exception:
        pass
    try:
        from datetime import datetime

        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def _duration_seconds(started_at: Any, finished_at: Any) -> float | None:
    start = _parse_time(started_at)
    finish = _parse_time(finished_at)
    if start is None or finish is None:
        return None
    return max(0.0, round(finish - start, 3))


def _task_sort_key(task: dict[str, Any]) -> float:
    value = task.get("updated_at") or task.get("submitted_at") or task.get("archived_at")
    if isinstance(value, (int, float)):
        return float(value)
    parsed = _parse_time(value)
    return parsed or 0.0


def _archive_list_rank(task: dict[str, Any]) -> tuple[int, int, float]:
    status = str(task.get("status") or "")
    return (
        1 if _is_closed_status(status) and _task_has_graph(task) else 0,
        1 if _task_has_graph(task) else 0,
        _task_sort_key(task),
    )


def _graph_id_from_workflow_id(workflow_id: str) -> str:
    if workflow_id.startswith("task-"):
        parts = workflow_id.removeprefix("task-").split("-")
        if len(parts) > 5:
            return "-".join(parts[:-5])
    return workflow_id


def _graph_name_from_workflow_id(workflow_id: str) -> str:
    graph_id = _graph_id_from_workflow_id(workflow_id)
    return f"Recovered task - {graph_id}"
