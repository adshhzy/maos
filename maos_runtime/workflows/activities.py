"""Temporal activities for dispatching and polling Agent nodes."""

from typing import Any

from temporalio import activity

from maos_runtime.a2a import (
    poll_task,
    resume_task_with_human_response,
    send_message,
    task_artifacts,
)
from maos_runtime.a2a.messages import _dependency_artifacts_for_message
from maos_runtime.a2a.runtime_config_helpers import _dependency_artifact_transfer_mode
from maos_runtime.graph.control_flow import _public_node_spec
from maos_runtime.sandbox.task_archive import archive_workflow_result


@activity.defn
async def dispatch_agent_node(activity_input: dict[str, Any]) -> dict[str, Any]:
    node = activity_input["node"]
    dependency_executions = activity_input["dependency_executions"]
    artifact_transfer_mode = _dependency_artifact_transfer_mode(node)
    dependency_artifacts = _dependency_artifacts_for_executions(
        dependency_executions,
        transfer_mode=artifact_transfer_mode,
    )
    previous_self_node = _previous_self_result_node(node, dependency_executions)
    reference_task_ids = [
        execution["a2a_task"]["id"] for execution in dependency_executions.values()
    ]
    activity.heartbeat({"phase": "agent-invoke", "node_id": node["id"]})
    message = {
        "messageId": f"temporal-msg-{node['id']}-{node.get('_visit', 1)}",
        "contextId": activity_input["context_id"],
        "role": "ROLE_USER",
        "parts": [
            {
                "data": {
                    "node": _public_node_spec(node),
                    "dependency_artifacts": dependency_artifacts,
                    "graph_input": activity_input.get("graph_input", {}),
                    "control_flow": {
                        "visit": node.get("_visit", 1),
                        "instance_id": node.get("_instance_id"),
                        "triggered_by": sorted(dependency_executions),
                        **(
                            {"previous_self_result_node": previous_self_node}
                            if previous_self_node
                            else {}
                        ),
                    },
                    "artifact_transfer": {
                        "mode": artifact_transfer_mode,
                    },
                },
                "mediaType": "application/json",
            }
        ],
        "referenceTaskIds": reference_task_ids,
        "metadata": {
            "protocol": "A2A",
            "purpose": "dependency-data-transfer",
            "operation": node.get("operation", "merge"),
            "visit": node.get("_visit", 1),
            "instance_id": node.get("_instance_id"),
        },
    }
    return send_message(
        {
            "message": message,
            "configuration": {"returnImmediately": True},
            "metadata": {
                "binding": "local-jsonrpc-style",
                "workflow_id": activity_input["workflow_id"],
                "node_id": node["id"],
                "instance_id": node.get("_instance_id"),
                "idempotency_key": activity_input["idempotency_key"],
            },
        }
    )


def _dependency_artifacts_for_executions(
    dependency_executions: dict[str, Any],
    *,
    transfer_mode: str = "ref",
) -> list[dict[str, Any]]:
    """Load complete upstream artifacts before injecting dependency data.

    Workflow history intentionally stores compact executions, so the artifacts
    inside ``dependency_executions`` may contain truncated text. Activities are
    short-lived and can read the provider-local A2A task store by task id; use
    that full artifact when available, then fall back to the compact snapshot.
    """

    artifacts: list[dict[str, Any]] = []
    for execution in dependency_executions.values():
        a2a_task = execution.get("a2a_task") if isinstance(execution, dict) else None
        if not isinstance(a2a_task, dict):
            continue
        loaded = _full_artifacts_for_task(a2a_task)
        artifacts.extend(loaded if loaded is not None else a2a_task.get("artifacts", []))
    return _dependency_artifacts_for_message(artifacts, transfer_mode=transfer_mode)


def _previous_self_result_node(node: dict[str, Any], dependency_executions: dict[str, Any]) -> str | None:
    node_id = str(node["id"])
    if int(node.get("_visit", 1) or 1) <= 1:
        return None
    return node_id if node_id in dependency_executions else None


def _full_artifacts_for_task(a2a_task: dict[str, Any]) -> list[dict[str, Any]] | None:
    task_id = a2a_task.get("id")
    if not task_id:
        return None
    try:
        response = task_artifacts({"id": task_id})
    except Exception:
        return None
    artifacts = response.get("artifacts")
    return artifacts if isinstance(artifacts, list) else None


@activity.defn
async def poll_agent_node(activity_input: dict[str, Any]) -> dict[str, Any]:
    activity.heartbeat(
        {
            "phase": "agent-status-poll",
            "node_id": activity_input["node_id"],
            "a2a_task_id": activity_input["id"],
        }
    )
    return poll_task(activity_input)


@activity.defn
async def resume_agent_node_human_intervention(
    activity_input: dict[str, Any],
) -> dict[str, Any]:
    activity.heartbeat(
        {
            "phase": "agent-human-response",
            "node_id": activity_input["node_id"],
            "a2a_task_id": activity_input["id"],
            "intervention_id": activity_input["intervention_id"],
        }
    )
    return resume_task_with_human_response(activity_input)


@activity.defn
async def archive_completed_workflow(activity_input: dict[str, Any]) -> dict[str, Any]:
    workflow_id = activity_input["workflow_id"]
    activity.heartbeat({"phase": "workflow-final-archive", "workflow_id": workflow_id})
    try:
        return archive_workflow_result(
            workflow_id=workflow_id,
            result=activity_input["result"],
            submitted_at=activity_input.get("submitted_at"),
            updated_at=activity_input.get("updated_at"),
        )
    except Exception as exc:
        return {
            "ok": False,
            "workflow_id": workflow_id,
            "error": str(exc),
        }


ACTIVITIES = [
    dispatch_agent_node,
    poll_agent_node,
    resume_agent_node_human_intervention,
    archive_completed_workflow,
]
