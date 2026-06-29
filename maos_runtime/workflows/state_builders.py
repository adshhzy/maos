"""State builders for JSON control-flow workflow visualization."""

from __future__ import annotations

from typing import Any

from maos_runtime.graph.control_flow import (
    _edge_label,
    _node_backend_for_display,
    _node_max_visits,
    _node_type,
)


def build_edge_state(edge: dict[str, Any]) -> dict[str, Any]:
    return {
        "from": edge["from"],
        "to": edge["to"],
        "label": edge.get("label") or _edge_label(edge),
        "when": edge.get("when"),
        "taken_count": 0,
        "skipped_count": 0,
    }


def build_node_state(node: dict[str, Any], graph_default_join: str = "all") -> dict[str, Any]:
    return {
        "id": node["id"],
        "label": node.get("label", node["id"]),
        "type": _node_type(node),
        "operation": node.get("operation", "merge"),
        "deps": node.get("deps", []),
        "join": node.get("join", graph_default_join),
        "status": "pending",
        "started_at": None,
        "finished_at": None,
        "duration_seconds": None,
        "planned_duration_seconds": None,
        "elapsed_seconds": 0,
        "heartbeat_count": 0,
        "last_heartbeat_at": None,
        "simulator_job_id": None,
        "agent_service_task_id": None,
        "hermes_job_id": None,
        "hermes_prompt": None,
        "codex_prompt": None,
        "codex_command": None,
        "codex_workdir": None,
        "claude_task_id": None,
        "claude_prompt": None,
        "claude_command": None,
        "claude_workdir": None,
        "agent_status": None,
        "backend": _node_backend_for_display(node),
        "completion_mode": None,
        "a2a_task_id": None,
        "a2a_state": None,
        "agent_name": None,
        "summary": "",
        "visits": 0,
        "max_visits": _node_max_visits(node),
        "current_instance_id": None,
        "instances": [],
        "human_interventions": [],
    }


def build_instance_state(
    *,
    node_spec: dict[str, Any],
    kind: str,
    started_at: str,
    backend: Any = None,
) -> dict[str, Any]:
    return {
        "id": node_spec["_instance_id"],
        "node_id": node_spec["id"],
        "visit": node_spec.get("_visit", 1),
        "kind": kind,
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "backend": backend,
        "agent_name": None,
        "a2a_task_id": None,
        "summary": "",
    }


def build_start_execution() -> dict[str, Any]:
    return {
        "status": "completed",
        "result": {
            "node": "__start__",
            "operation": "start",
            "duration_seconds": 0,
            "payload": {"status": "completed"},
        },
        "a2a_task": {"id": "__start__", "artifacts": []},
    }
