"""A2A message, task-event, and artifact helpers."""

from __future__ import annotations

import uuid
from typing import Any

from maos_runtime.a2a.artifact_store import store_dependency_artifact
from maos_runtime.a2a_constants import SIMULATOR_BACKEND


DEPENDENCY_LIST_LIMIT = 10


def _artifact_from_result(
    result: dict[str, Any],
    metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "artifactId": f"artifact-{uuid.uuid4()}",
        "name": "dag-node-result",
        "description": f"Result artifact for {metadata['nodeId']}",
        "parts": [{"data": result, "mediaType": "application/json"}],
        "metadata": {
            "nodeId": metadata["nodeId"],
            "operation": metadata["operation"],
            "workflow_id": metadata.get("workflow_id"),
            "workflowId": metadata.get("workflow_id"),
            "a2aTaskId": metadata.get("a2aTaskId")
            or metadata.get("claudeTaskId")
            or metadata.get("codexTaskId")
            or metadata.get("evaluationTaskId"),
            "contextId": metadata.get("contextId"),
            "producedByAgent": metadata["agentCard"]["name"],
            "backend": metadata.get("backend", SIMULATOR_BACKEND),
            "simulatorJobId": metadata.get("simulatorJobId"),
            "agentServiceTaskId": metadata.get("agentServiceTaskId"),
            "hermesJobId": metadata.get("hermesJobId"),
            "contextPolicy": metadata.get("contextPolicy"),
            "runtimeProfile": metadata.get("runtimeProfile"),
            "executionMode": metadata.get("executionMode"),
        },
    }


def _dependency_artifacts_for_message(
    artifacts: list[dict[str, Any]],
    *,
    transfer_mode: str = "ref",
    workflow_id: str | None = None,
    consumer_node_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return A2A artifacts for dependency transfer.

    ``ref`` mode stores dependency content externally and sends only an
    artifact reference. ``inline`` keeps the previous full payload injection
    behavior for Agent services that cannot fetch artifact URLs.
    """

    mode = "inline" if str(transfer_mode).lower() == "inline" else "ref"
    dependency_artifacts: list[dict[str, Any]] = []
    for artifact in artifacts:
        if artifact.get("name") != "dag-node-result":
            dependency_artifacts.append(artifact)
            continue

        result = _first_data_part({"parts": artifact.get("parts", [])})
        compact_result = _compact_dependency_result(result)
        if mode == "inline":
            data = compact_result
            metadata = {
                **(artifact.get("metadata") or {}),
                "compactDependencyArtifact": True,
                "dependencyTransferMode": "inline",
                "sourceArtifactId": artifact.get("artifactId"),
            }
        else:
            artifact_metadata = {
                **(artifact.get("metadata") or {}),
                "producer_workflow_id": _artifact_workflow_id(artifact) or workflow_id,
                "consumer_workflow_id": workflow_id,
                "consumer_node_id": consumer_node_id,
            }
            ref = store_dependency_artifact(
                compact_result,
                source_artifact_id=artifact.get("artifactId"),
                metadata=artifact_metadata,
            )
            data = _dependency_result_ref(compact_result, ref)
            metadata = {
                **(artifact.get("metadata") or {}),
                "compactDependencyArtifact": True,
                "dependencyTransferMode": "ref",
                "sourceArtifactId": artifact.get("artifactId"),
                "artifactRef": ref["artifact_ref"],
                "artifactUri": ref["uri"],
                "contentHash": ref["content_hash"],
                "producerWorkflowId": artifact_metadata.get("producer_workflow_id"),
                "consumerWorkflowId": workflow_id,
            }

        dependency_artifacts.append(
            {
                **artifact,
                "parts": [{"data": data, "mediaType": "application/json"}],
                "metadata": metadata,
            }
        )
    return dependency_artifacts


def _dependency_results_from_artifacts(
    artifacts: list[dict[str, Any]],
    *,
    resolve_refs: bool = False,
    expected_workflow_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    dependency_results: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        if artifact.get("name") != "dag-node-result":
            continue
        result = _first_data_part({"parts": artifact.get("parts", [])})
        if isinstance(result, dict) and "node" not in result:
            node_id = (artifact.get("metadata") or {}).get("nodeId")
            if node_id:
                result = {
                    "node": node_id,
                    "operation": (artifact.get("metadata") or {}).get("operation"),
                    "payload": result,
                }
        if not isinstance(result, dict) or "node" not in result:
            continue
        if resolve_refs and _result_has_artifact_ref(result):
            result = _resolve_dependency_result_ref(
                result,
                expected_workflow_id=expected_workflow_id,
            )
        dependency_results[result["node"]] = result
    return dependency_results


def _compact_dependency_result(result: dict[str, Any]) -> dict[str, Any]:
    compact = {
        "node": result.get("node"),
        "operation": result.get("operation"),
        "duration_seconds": result.get("duration_seconds"),
        "payload": _compact_dependency_payload(result.get("payload", {})),
    }
    return {key: value for key, value in compact.items() if value is not None}


def _dependency_result_ref(
    compact_result: dict[str, Any],
    artifact_ref: dict[str, Any],
) -> dict[str, Any]:
    return {
        "node": compact_result.get("node"),
        "operation": compact_result.get("operation"),
        "duration_seconds": compact_result.get("duration_seconds"),
        "payload": {
            "artifact_ref": artifact_ref["artifact_ref"],
            "uri": artifact_ref["uri"],
            "content_hash": artifact_ref["content_hash"],
            "mime_type": artifact_ref["mime_type"],
            "size": artifact_ref["size"],
            "summary": artifact_ref.get("summary", ""),
        },
    }


def _result_has_artifact_ref(result: dict[str, Any]) -> bool:
    payload = result.get("payload") if isinstance(result, dict) else None
    return isinstance(payload, dict) and isinstance(payload.get("artifact_ref"), str)


def _resolve_dependency_result_ref(
    result: dict[str, Any],
    *,
    expected_workflow_id: str | None = None,
) -> dict[str, Any]:
    payload = result.get("payload") or {}
    try:
        artifact = load_artifact_content_record(payload.get("artifact_ref"))
    except Exception:
        return result
    if expected_workflow_id:
        metadata = artifact.get("metadata") if isinstance(artifact, dict) else {}
        producer_workflow_id = (
            metadata.get("producer_workflow_id")
            or metadata.get("workflow_id")
            or metadata.get("workflowId")
        )
        consumer_workflow_id = metadata.get("consumer_workflow_id")
        if producer_workflow_id and producer_workflow_id != expected_workflow_id:
            return _scoped_ref_rejection(result, producer_workflow_id, expected_workflow_id)
        if consumer_workflow_id and consumer_workflow_id != expected_workflow_id:
            return _scoped_ref_rejection(result, consumer_workflow_id, expected_workflow_id)
    content = artifact.get("content") if isinstance(artifact, dict) else None
    if not isinstance(content, dict):
        return result
    resolved = dict(content)
    resolved_payload = resolved.setdefault("payload", {})
    if isinstance(resolved_payload, dict):
        for key in ("artifact_ref", "uri", "content_hash", "mime_type", "size"):
            if key in payload:
                resolved_payload.setdefault(key, payload[key])
    return resolved


def load_artifact_content_record(artifact_ref: str) -> dict[str, Any]:
    from maos_runtime.a2a.artifact_store import load_artifact

    return load_artifact(artifact_ref, include_content=True)


def _scoped_ref_rejection(
    result: dict[str, Any],
    artifact_workflow_id: str,
    expected_workflow_id: str,
) -> dict[str, Any]:
    rejected = dict(result)
    payload = dict(rejected.get("payload") or {})
    payload["artifact_scope_error"] = (
        "artifact_ref belongs to a different workflow run and was not expanded"
    )
    payload["artifact_workflow_id"] = artifact_workflow_id
    payload["expected_workflow_id"] = expected_workflow_id
    rejected["payload"] = payload
    return rejected


def _artifact_workflow_id(artifact: dict[str, Any]) -> str | None:
    metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
    value = metadata.get("workflow_id") or metadata.get("workflowId")
    return str(value) if value else None


def _compact_dependency_payload(payload: Any) -> Any:
    if not isinstance(payload, dict):
        return _compact_dependency_value(payload)

    omitted: list[str] = []
    compact: dict[str, Any] = {}
    summary = payload.get("latest_comment") or payload.get("stdout") or payload.get("summary")
    if summary:
        compact["summary"] = _compact_dependency_value(summary)
    for key, value in payload.items():
        if key in {
            "dependencies",
            "dependency_results",
            "dependency_artifacts",
            "history",
            "messages",
            "comments",
            "runs",
            "trace",
            "human_interventions",
            "latest_comment",
            "stdout",
            "summary",
        }:
            if key != "summary":
                omitted.append(str(key))
            continue
        compact[str(key)] = _compact_dependency_value(value)
    if omitted:
        compact["_omitted_dependency_fields"] = omitted
    return compact


def _compact_dependency_value(value: Any) -> Any:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return [_compact_dependency_value(item) for item in value[-DEPENDENCY_LIST_LIMIT:]]
    if isinstance(value, dict):
        return {
            str(key): _compact_dependency_value(item)
            for key, item in value.items()
            if str(key)
            not in {
                "raw",
                "history",
                "full_text",
                "transcript",
                "dependency_artifacts",
            }
        }
    return value


def _first_data_part(message: dict[str, Any]) -> dict[str, Any]:
    for part in message.get("parts", []):
        if "data" in part:
            return part["data"]
    raise ValueError("A2A message does not contain a data part")


def _agent_message(task_id: str, context_id: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "messageId": f"msg-{uuid.uuid4()}",
        "contextId": context_id,
        "taskId": task_id,
        "role": "ROLE_AGENT",
        "parts": [{"data": data, "mediaType": "application/json"}],
        "metadata": {"runtime": "sandbox-agent-runtime"},
    }


def _event_from_a2a_task(callback: dict[str, Any], completed_task: dict[str, Any]) -> dict[str, Any]:
    metadata = completed_task.get("metadata", {})
    human_request = (
        callback.get("human_request")
        or callback.get("input_request")
        or metadata.get("humanRequest")
    )
    return {
        "workflow_id": callback.get("workflow_id"),
        "node_id": callback["node_id"],
        "a2a_task_id": completed_task["id"],
        "status": callback["status"],
        "error": callback.get("error"),
        "a2a_state": completed_task["status"]["state"],
        "a2a_task": completed_task,
        "human_request": human_request,
        "human_request_id": (
            callback.get("human_request_id")
            or metadata.get("humanRequestId")
            or (human_request or {}).get("request_id")
        ),
    }
