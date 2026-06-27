"""Preview projections for graphs before workflow execution."""

from typing import Any

from maos_runtime.dag_workflow import normalize_graph


def preview_state(graph: dict[str, Any]) -> dict[str, Any]:
    spec = normalize_graph(graph)
    return {
        "graph_id": spec.get("id", ""),
        "graph_name": spec.get("name", spec["id"]),
        "graph_type": spec.get("graph_type", "control_flow"),
        "workflow_status": "queued",
        "levels": spec["levels"],
        "edges": spec["edges"],
        "nodes": [
            {
                "id": node["id"],
                "label": node.get("label", node["id"]),
                "type": node.get("type", "agent"),
                "operation": node.get("operation", "merge"),
                "deps": node.get("deps", []),
                "join": node.get("join", spec.get("default_join", "all")),
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
                "agent_status": None,
                "backend": _node_backend_for_display(node),
                "completion_mode": None,
                "a2a_task_id": None,
                "a2a_state": None,
                "agent_name": None,
                "summary": "",
                "visits": 0,
                "max_visits": int(node.get("max_visits", node.get("max_attempts", 1))),
                "current_instance_id": None,
                "instances": [],
            }
            for node in spec["nodes"]
        ],
        "instances": [],
    }

def build_preview_levels(nodes: list[dict[str, Any]]) -> list[list[str]]:
    remaining = {node["id"]: set(node.get("deps", [])) for node in nodes}
    completed: set[str] = set()
    levels: list[list[str]] = []
    while remaining:
        ready = sorted(
            node_id
            for node_id, deps in remaining.items()
            if deps.issubset(completed)
        )
        if not ready:
            return [sorted(remaining)]
        levels.append(ready)
        completed.update(ready)
        for node_id in ready:
            remaining.pop(node_id)
    return levels

def _node_backend_for_display(node: dict[str, Any]) -> str:
    node_type = str(node.get("type") or ("condition" if node.get("operation") == "condition" else "agent")).lower()
    if node_type in {"condition", "decision", "router", "branch"}:
        return "control-flow"
    agent = node.get("agent")
    agent_config = agent if isinstance(agent, dict) else {}
    backend = (
        agent_config.get("backend")
        or node.get("backend")
        or node.get("runtime")
        or "simulator"
    )
    normalized = str(backend).lower().replace("_", "-")
    if normalized in {"agent-service", "agentservice", "real-agent", "multica-daemon"}:
        return "multica"
    if normalized in {"sim", "mock", "simulator"}:
        return "simulator"
    if normalized in {"hermes", "hermes-oneshot", "direct-hermes", "hermes-direct"}:
        return "hermes"
    return normalized
