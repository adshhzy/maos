"""Workflow-history compaction helpers.

Temporal workflow history should not carry full agent traces, prompts, or large
artifacts. These helpers keep the deterministic workflow state small while
preserving enough information for retries, branch decisions, and UI summaries.
"""

from __future__ import annotations

from typing import Any


WORKFLOW_HISTORY_TEXT_LIMIT = 8000
WORKFLOW_HISTORY_LIST_LIMIT = 20


def compact_execution_for_history(execution: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "status": execution.get("status"),
        "instance_id": execution.get("instance_id"),
    }
    if execution.get("error") is not None:
        compact["error"] = compact_value_for_history(execution.get("error"))
    if isinstance(execution.get("result"), dict):
        compact["result"] = compact_value_for_history(execution["result"])
    if isinstance(execution.get("a2a_task"), dict):
        compact["a2a_task"] = compact_a2a_task_for_history(execution["a2a_task"])
    return {key: value for key, value in compact.items() if value is not None}


def compact_dependency_executions_for_activity(
    dependency_executions: dict[str, Any],
) -> dict[str, Any]:
    return {
        str(node_id): compact_execution_for_history(execution)
        for node_id, execution in dependency_executions.items()
    }


def compact_a2a_task_for_history(task: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "id": task.get("id"),
        "status": compact_value_for_history(task.get("status") or {}),
        "metadata": compact_value_for_history(task.get("metadata") or {}),
    }
    artifacts = task.get("artifacts")
    if isinstance(artifacts, list):
        compact["artifacts"] = [
            compact_artifact_for_history(item)
            for item in artifacts[-WORKFLOW_HISTORY_LIST_LIMIT:]
        ]
    return {key: value for key, value in compact.items() if value not in (None, {}, [])}


def compact_artifact_for_history(artifact: Any) -> Any:
    if not isinstance(artifact, dict):
        return compact_value_for_history(artifact)
    return {
        "artifactId": artifact.get("artifactId"),
        "name": artifact.get("name"),
        "description": compact_value_for_history(artifact.get("description")),
        "metadata": compact_value_for_history(artifact.get("metadata") or {}),
        "parts": compact_value_for_history(artifact.get("parts") or []),
    }


def compact_payload_for_history(payload: Any) -> Any:
    return compact_value_for_history(payload)


def compact_value_for_history(value: Any) -> Any:
    if isinstance(value, str):
        if len(value) <= WORKFLOW_HISTORY_TEXT_LIMIT:
            return value
        return value[:WORKFLOW_HISTORY_TEXT_LIMIT] + "...<truncated>"
    if isinstance(value, list):
        return [
            compact_value_for_history(item)
            for item in value[-WORKFLOW_HISTORY_LIST_LIMIT:]
        ]
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        omitted: list[str] = []
        for key, item in value.items():
            key_text = str(key)
            if key_text in {
                "raw",
                "history",
                "full_text",
                "transcript",
                "messages",
                "comments",
                "runs",
                "trace",
                "dependency_results",
                "dependency_artifacts",
            }:
                omitted.append(key_text)
                continue
            compact[key_text] = compact_value_for_history(item)
        if omitted:
            compact["_omitted_for_workflow_history"] = omitted
        return compact
    return value
