"""Stable Agent Provider API v1.

``ProviderRuntime`` is the concrete base class for provider implementations. It
owns the common lifecycle surface: capabilities, create/send, poll, cancel,
resume, events, artifacts, and access to the local A2A task store.
"""

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


class ProviderRuntime:
    """Base runtime for one concrete Agent backend.

    Workflow code deals in A2A-shaped tasks. Providers hide each backend's
    create/poll/cancel/resume/events/artifacts details behind this stable v1
    contract.

    Subclasses normally implement ``agent_card``, ``_create`` and ``_poll``.
    Optional operations are enabled by setting the ``supports_*`` flags and
    implementing ``_cancel`` or ``_resume``. Older ``send`` and
    ``resume_human_response`` names remain as compatibility aliases for
    existing workflow activities.
    """

    backend = ""
    api_version = "agent-provider-v1"
    supports_cancel = False
    supports_resume = False
    supports_push_callbacks = False

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def capabilities(self) -> dict[str, Any]:
        operations = dict(DEFAULT_PROVIDER_CAPABILITIES)
        operations.update(
            {
                "cancel": self.supports_cancel,
                "resume": self.supports_resume,
                "push_callbacks": self.supports_push_callbacks,
            }
        )
        return {
            "api_version": self.api_version,
            "backend": self.backend,
            "operations": operations,
        }

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._create(request)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(f"{self.backend} provider does not implement create.")

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.create(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return self._poll(task_id, request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(f"{self.backend} provider does not implement poll.")

    def cancel(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.supports_cancel:
            raise NotImplementedError(f"{self.backend} provider does not support cancel.")
        return self._cancel(task_id, request)

    def _cancel(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(f"{self.backend} provider does not implement cancel.")

    def resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.supports_resume:
            raise NotImplementedError(f"{self.backend} provider does not support human-input resume.")
        return self._resume(task_id, request)

    def _resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError(f"{self.backend} provider does not implement resume.")

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
        task = self.task_from_request_or_store(task_id, request)
        return {
            "task_id": task_id,
            "events": [{"seq": 0, "type": "task_snapshot", "task": task}],
        }

    def artifacts(
        self,
        task_id: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        task = self.task_from_request_or_store(task_id, request)
        return {
            "task_id": task_id,
            "artifacts": copy.deepcopy(task.get("artifacts") or []),
        }

    def task_from_request_or_store(
        self,
        task_id: str,
        request: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return _task_from_request_or_store(task_id, request)

    def task_record_from_store(self, task_id: str) -> dict[str, Any]:
        from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK

        with TASKS_LOCK:
            record = TASKS.get(task_id)
            if not record:
                raise KeyError(task_id)
            return record

    def cancel_local_task(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.agent_service_client import _timestamp

        with _task_store_lock():
            record = self.task_record_from_store(task_id)
            task = record["task"]
            metadata = task.setdefault("metadata", {})
            metadata["cancelReason"] = request.get("reason")
            metadata["cancelledAt"] = _timestamp()
            task["status"] = {
                "state": "TASK_STATE_CANCELED",
                "message": {
                    "role": "ROLE_AGENT",
                    "parts": [
                        {
                            "data": {
                                "status": "cancelled",
                                "reason": request.get("reason"),
                            },
                            "mediaType": "application/json",
                        }
                    ],
                },
                "timestamp": _timestamp(),
            }
            cancelled = copy.deepcopy(task)
        return {"ok": True, "task": cancelled}

    def cancel_agent_service_task(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        record = self.task_record_from_store(task_id)
        task = record["task"]
        metadata = task["metadata"]
        agent_task_id = metadata["agentServiceTaskId"]

        from maos_runtime.http_json_client import request_json
        from maos_runtime.runtime_config import agent_service_api_base

        projection = request_json(
            agent_service_api_base(),
            "POST",
            f"/api/v1/agent-tasks/{agent_task_id}/cancel",
            {
                "reason": request.get("reason"),
                "requested_by": request.get("requested_by", "maos"),
            },
        )
        local = self.cancel_local_task(task_id, request)
        local["agent_task"] = projection
        return local


class AgentRuntimeProvider(ProviderRuntime):
    """Backward-compatible name for provider implementations."""

    pass


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


def _task_store_lock():
    from maos_runtime.a2a_task_store import TASKS_LOCK

    return TASKS_LOCK
