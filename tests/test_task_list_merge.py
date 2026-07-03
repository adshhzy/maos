from __future__ import annotations

from pathlib import Path
from typing import Any

from maos_runtime.persistence import persist_workflow_task_snapshot
from maos_runtime.sandbox.task_list import snapshot_with_execution_store_tasks
from services.sandbox_api_service import task_list_snapshot_with_execution_store_merge


def test_task_list_adds_execution_store_only_tasks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MAOS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MAOS_EXECUTION_DB_FILE", raising=False)
    persist_workflow_task_snapshot(
        _task(
            "task-store-only",
            status="completed",
            updated_at="2026-01-01T00:00:00Z",
            node_count=1,
        ),
        source="test",
    )

    snapshot = snapshot_with_execution_store_tasks({"runtime_status": "ready", "tasks": []})

    assert snapshot["task_list_mode"] == "execution_store_primary"
    assert snapshot["task_sources"]["live"] == 0
    assert snapshot["task_sources"]["execution_store"] == 1
    assert snapshot["tasks"][0]["task_id"] == "task-store-only"
    assert snapshot["tasks"][0]["_list_sources"] == ["execution_store"]


def test_task_list_prefers_completed_store_graph_over_live_shell(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAOS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MAOS_EXECUTION_DB_FILE", raising=False)
    persist_workflow_task_snapshot(
        _task(
            "task-duplicate",
            status="completed",
            graph_name="Store Graph",
            updated_at="2026-01-01T00:00:10Z",
            node_count=2,
        ),
        source="test",
    )

    live_shell = {
        "task_id": "task-duplicate",
        "workflow_id": "task-duplicate",
        "graph_id": "task-duplicate",
        "graph_name": "Live Shell",
        "status": "running",
        "updated_at": "2026-01-01T00:00:20Z",
        "state": {"nodes": [], "edges": []},
    }
    snapshot = snapshot_with_execution_store_tasks(
        {"runtime_status": "running", "tasks": [live_shell]}
    )

    task = snapshot["tasks"][0]
    assert task["task_id"] == "task-duplicate"
    assert task["status"] == "completed"
    assert task["graph_name"] == "Store Graph"
    assert task["_list_sources"] == ["execution_store", "live"]
    assert snapshot["task_sources"]["skipped"] == 1
    assert snapshot["task_sources"]["refreshed"] == 0
    assert snapshot["task_sources"]["duplicates"] == 1
    assert snapshot["task_sources"]["store_replacements"] == 1


def test_task_list_keeps_live_running_graph_over_older_running_store(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAOS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MAOS_EXECUTION_DB_FILE", raising=False)
    persist_workflow_task_snapshot(
        _task(
            "task-running",
            status="running",
            graph_name="Older Store Graph",
            updated_at="2026-01-01T00:00:00Z",
            node_count=1,
        ),
        source="test",
    )

    live = _task(
        "task-running",
        status="running",
        graph_name="Live Graph",
        updated_at="2026-01-01T00:01:00Z",
        node_count=1,
    )
    snapshot = snapshot_with_execution_store_tasks(
        {"runtime_status": "running", "tasks": [live]}
    )

    assert snapshot["tasks"][0]["graph_name"] == "Live Graph"
    assert snapshot["tasks"][0]["_list_sources"] == ["execution_store", "live"]
    assert snapshot["task_sources"]["refreshed"] == 1


def test_task_list_uses_execution_store_when_manager_snapshot_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("MAOS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MAOS_EXECUTION_DB_FILE", raising=False)
    persist_workflow_task_snapshot(
        _task(
            "task-manager-failed",
            status="completed",
            updated_at="2026-01-01T00:00:00Z",
            node_count=1,
        ),
        source="test",
    )

    snapshot = task_list_snapshot_with_execution_store_merge(_FailingManager())

    assert snapshot["runtime_status"] == "failed"
    assert "temporal unavailable" in snapshot["error"]
    assert snapshot["tasks"][0]["task_id"] == "task-manager-failed"


def _task(
    task_id: str,
    *,
    status: str,
    updated_at: str,
    graph_name: str | None = None,
    node_count: int = 0,
) -> dict[str, Any]:
    nodes = [
        {"id": f"node_{index}", "status": status}
        for index in range(node_count)
    ]
    state = {
        "graph_id": task_id.replace("task-", "graph-"),
        "graph_name": graph_name or task_id,
        "workflow_status": status,
        "nodes": nodes,
        "edges": [],
    }
    return {
        "task_id": task_id,
        "workflow_id": task_id,
        "graph_id": state["graph_id"],
        "graph_name": state["graph_name"],
        "status": status,
        "updated_at": updated_at,
        "state": state,
        "result": {"status": status, "state": state},
    }


class _FailingManager:
    def snapshot(self) -> dict[str, Any]:
        raise RuntimeError("temporal unavailable")

    def runtime_info(self) -> dict[str, Any]:
        return {
            "runtime_status": "failed",
            "temporal": {"mode": "external", "target": "127.0.0.1:7233"},
        }
