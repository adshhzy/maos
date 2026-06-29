"""Human-intervention state helpers for control-flow workflows."""

from __future__ import annotations

from typing import Any


def build_human_intervention(
    *,
    workflow_id: str,
    node_spec: dict[str, Any],
    intervention_id: str,
    prompt: str,
    intervention_type: str,
    schema: dict[str, Any],
    assignee: Any = None,
    assignee_role: Any = None,
    upstream_node_ids: list[str] | None = None,
    created_at: str,
    source: str = "graph",
    human_request: dict[str, Any] | None = None,
    a2a_task_id: str | None = None,
) -> dict[str, Any]:
    return {
        "id": intervention_id,
        "intervention_id": intervention_id,
        "workflow_id": workflow_id,
        "node_id": node_spec["id"],
        "node_label": node_spec.get("label", node_spec["id"]),
        "instance_id": node_spec["_instance_id"],
        "visit": node_spec.get("_visit", 1),
        "type": intervention_type,
        "source": source,
        "status": "pending",
        "prompt": prompt,
        "schema": schema,
        "assignee": assignee,
        "assignee_role": assignee_role,
        "created_at": created_at,
        "responded_at": None,
        "responder": None,
        "response": None,
        "decision": None,
        "comment": None,
        "upstream_node_ids": upstream_node_ids or [],
        "human_request": human_request or {},
        "human_request_id": (human_request or {}).get("request_id"),
        "a2a_task_id": a2a_task_id,
    }


def human_result_payload(
    *,
    intervention_id: str,
    response_event: dict[str, Any],
    resolved: dict[str, Any],
    upstream_node_ids: list[str],
) -> dict[str, Any]:
    response_payload = response_event.get("response", {})
    if not isinstance(response_payload, dict):
        response_payload = {"value": response_payload}
    payload = {
        "status": "completed",
        "human_intervention_id": intervention_id,
        "human_intervention_type": resolved.get("type"),
        "decision": response_event.get("decision") or response_payload.get("decision"),
        "comment": response_event.get("comment") or response_payload.get("comment"),
        "responder": response_event.get("responder"),
        "response": response_payload,
        "human_interventions": [resolved],
        "upstream_node_ids": upstream_node_ids,
    }
    payload.update({key: value for key, value in response_payload.items() if key not in payload})
    return payload


def node_human_interventions(
    human_interventions: dict[str, dict[str, Any]],
    node_id: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    return [
        intervention
        for intervention in human_interventions.values()
        if intervention.get("node_id") == node_id
    ][-limit:]
