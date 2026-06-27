"""Stable Agent Provider API v1."""

from __future__ import annotations

import copy
from typing import Any


DEFAULT_PROVIDER_CAPABILITIES: dict[str, bool] = {
    "create": True,
    "poll": True,
    "cancel": False,
    "resume": False,
    "events": True,
    "artifacts": True,
    "push_callbacks": False,
}


class AgentRuntimeProvider:
    """Adapter interface for one concrete Agent backend.

    Workflow code deals in A2A-shaped tasks. Providers hide each backend's
    create/poll/cancel/resume/events/artifacts details behind this stable v1
    contract. Older ``send`` and ``resume_human_response`` names remain as
    compatibility aliases for existing workflow activities.
    """

    backend = ""
    api_version = "agent-provider-v1"

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def capabilities(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "backend": self.backend,
            "operations": dict(DEFAULT_PROVIDER_CAPABILITIES),
        }

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.create(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def cancel(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    def resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError

    def resume_human_response(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        return self.resume(task_id, request)

    def events(
        self,
        task_id: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = _task_from_request_or_store(task_id, request)
        return {
            "task_id": task_id,
            "events": [{"seq": 0, "type": "task_snapshot", "task": task}],
        }

    def artifacts(
        self,
        task_id: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = _task_from_request_or_store(task_id, request)
        return {
            "task_id": task_id,
            "artifacts": copy.deepcopy(task.get("artifacts") or []),
        }


def _task_from_request_or_store(
    task_id: str,
    request: dict[str, Any] | None,
) -> dict[str, Any]:
    task_hint = (request or {}).get("task")
    if isinstance(task_hint, dict):
        return copy.deepcopy(task_hint)

    from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK

    with TASKS_LOCK:
        record = TASKS.get(task_id)
        if not record:
            raise KeyError(task_id)
        return copy.deepcopy(record["task"])
