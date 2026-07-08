"""Task-list projection that merges live Temporal state with Execution Store."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from maos_runtime.persistence import (
    list_workflow_task_projections,
    load_workflow_task_snapshot,
    load_workflow_task_projection,
    persist_workflow_task_snapshot,
)
from maos_runtime.sandbox.api_projection import _task_list_item_for_api
from maos_runtime.sandbox.constants import (
    COMPLETED_WORKFLOW_DISPLAY_LIMIT,
    WORKFLOW_LIST_LIMIT,
)


_CLOSED_STATUSES = {"completed", "failed", "cancelled", "terminated", "timed_out"}
_ACTIVE_STATUSES = {"starting", "running"}


def snapshot_with_execution_store_tasks(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a task-list snapshot with Execution Store as the primary read source."""

    merged = dict(snapshot or {})
    live_tasks = [
        task
        for task in merged.get("tasks", [])
        if isinstance(task, dict)
    ]
    refresh_stats = refresh_execution_store_from_live_tasks(
        live_tasks,
        source="live_task_list_refresh",
    )
    store_tasks = execution_store_task_list_items()
    rows, stats = merge_task_list_rows(live_tasks, store_tasks)
    merged["tasks"] = rows
    merged["task_list_mode"] = "execution_store_primary"
    merged["task_sources"] = {
        "live": len(live_tasks),
        "execution_store": len(store_tasks),
        "merged": len(rows),
        **refresh_stats,
        **stats,
    }
    return merged


def refresh_execution_store_from_live_tasks(
    live_tasks: list[dict[str, Any]],
    *,
    source: str,
) -> dict[str, int]:
    """Project live snapshots into the store without degrading better rows."""

    stats = {"refreshed": 0, "skipped": 0, "refresh_failed": 0}
    for task in live_tasks:
        if not isinstance(task, dict):
            stats["skipped"] += 1
            continue
        task_id = _task_id(task)
        if not task_id:
            stats["skipped"] += 1
            continue
        existing = load_workflow_task_projection(task_id) or load_workflow_task_snapshot(task_id)
        if existing and _choose_task_row(existing, task) is existing:
            stats["skipped"] += 1
            continue
        try:
            persist_workflow_task_snapshot(task, source=source)
            stats["refreshed"] += 1
        except Exception:
            stats["refresh_failed"] += 1
    return stats


def execution_store_task_list_items(limit: int | None = None) -> list[dict[str, Any]]:
    """Load lightweight task rows from structured Execution Store projections."""

    rows: list[dict[str, Any]] = []
    for task in list_workflow_task_projections(limit=limit or _store_scan_limit()):
        workflow_id = str(task.get("workflow_id") or task.get("task_id") or "")
        if not workflow_id:
            continue
        item = _task_list_item_for_api(task)
        item.setdefault("task_id", workflow_id)
        item.setdefault("workflow_id", workflow_id)
        item.setdefault("graph_id", task.get("graph_id") or workflow_id)
        item.setdefault("graph_name", task.get("graph_name") or item.get("graph_id"))
        item.setdefault("status", task.get("status") or "unknown")
        item.setdefault("updated_at", task.get("updated_at"))
        item.setdefault("submitted_at", task.get("submitted_at"))
        item["_list_sources"] = ["execution_store"]
        rows.append(item)
    return rows


def merge_task_list_rows(
    live_tasks: list[dict[str, Any]],
    store_tasks: list[dict[str, Any]],
    *,
    completed_limit: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Deduplicate and sort task rows from live and persisted sources."""

    selected: dict[str, dict[str, Any]] = {}
    sources: dict[str, set[str]] = {}
    replaced = 0
    for source, tasks in (("live", live_tasks), ("execution_store", store_tasks)):
        for task in tasks:
            task_id = _task_id(task)
            if not task_id:
                continue
            candidate = dict(task)
            current = selected.get(task_id)
            source_set = sources.setdefault(task_id, set())
            source_set.add(source)
            if current is None:
                selected[task_id] = candidate
                continue
            chosen = _choose_task_row(current, candidate)
            if chosen is candidate:
                replaced += 1
            selected[task_id] = chosen

    rows: list[dict[str, Any]] = []
    for task_id, task in selected.items():
        item = dict(task)
        item["_list_sources"] = sorted(sources.get(task_id, set()))
        rows.append(item)
    rows.sort(key=_task_sort_key, reverse=True)

    visible = _apply_completed_limit(rows, completed_limit)
    return [_task_list_item_for_api(row) for row in visible], {
        "duplicates": len(live_tasks) + len(store_tasks) - len(selected),
        "store_replacements": replaced,
    }


def _choose_task_row(current: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    current_rank = _task_quality_rank(current)
    candidate_rank = _task_quality_rank(candidate)
    if candidate_rank > current_rank:
        return candidate
    if candidate_rank < current_rank:
        return current
    if _task_sort_key(candidate) >= _task_sort_key(current):
        return candidate
    return current


def _task_quality_rank(task: dict[str, Any]) -> tuple[int, int]:
    status = str(task.get("status") or "").lower()
    has_graph = 1 if _task_has_graph(task) else 0
    if status in _CLOSED_STATUSES and has_graph:
        return (4, has_graph)
    if status in _ACTIVE_STATUSES and has_graph:
        return (3, has_graph)
    if has_graph:
        return (2, has_graph)
    if status in _CLOSED_STATUSES:
        return (1, has_graph)
    return (0, has_graph)


def _apply_completed_limit(
    rows: list[dict[str, Any]],
    completed_limit: int | None,
) -> list[dict[str, Any]]:
    limit = _completed_display_limit() if completed_limit is None else completed_limit
    if limit < 0:
        return rows
    completed = 0
    visible: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("status") or "").lower() == "completed":
            if completed >= limit:
                continue
            completed += 1
        visible.append(row)
    return visible


def _task_id(task: dict[str, Any]) -> str:
    return str(task.get("task_id") or task.get("workflow_id") or "")


def _task_has_graph(task: dict[str, Any]) -> bool:
    state = task.get("state")
    if isinstance(state, dict) and (state.get("nodes") or state.get("edges")):
        return True
    result = task.get("result")
    if not isinstance(result, dict):
        return False
    result_state = result.get("state")
    if isinstance(result_state, dict) and (result_state.get("nodes") or result_state.get("edges")):
        return True
    return bool(result.get("results") or result.get("instance_results"))


def _task_sort_key(task: dict[str, Any]) -> float:
    for key in ("updated_at", "finished_at", "submitted_at", "archived_at"):
        value = task.get(key)
        parsed = _time_value(value)
        if parsed is not None:
            return parsed
    return 0.0


def _time_value(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        normalized = text.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return None


def _completed_display_limit() -> int:
    raw = os.environ.get(
        "SANDBOX_ARCHIVED_TASK_DISPLAY_LIMIT",
        str(COMPLETED_WORKFLOW_DISPLAY_LIMIT),
    )
    try:
        return int(raw)
    except ValueError:
        return COMPLETED_WORKFLOW_DISPLAY_LIMIT


def _store_scan_limit() -> int:
    raw = os.environ.get("SANDBOX_EXECUTION_STORE_TASK_SCAN_LIMIT")
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(WORKFLOW_LIST_LIMIT, COMPLETED_WORKFLOW_DISPLAY_LIMIT * 20)
