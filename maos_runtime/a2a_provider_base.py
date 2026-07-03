"""Stable Agent Provider API v1.

``ProviderRuntime`` is the concrete base class for provider implementations. It
owns the common lifecycle surface: capabilities, create/send, poll, cancel,
resume, events, artifacts, and access to the local A2A task store.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
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

TERMINAL_TASK_STATES = {
    "TASK_STATE_COMPLETED",
    "TASK_STATE_FAILED",
    "TASK_STATE_CANCELED",
    "TASK_STATE_CANCELLED",
}


@dataclass
class ProviderTaskContext:
    """Runtime context passed to a provider driver lifecycle hook."""

    backend: str
    request: dict[str, Any]
    task_id: str | None = None
    task: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderDriverResult:
    """Normalized result returned by provider driver lifecycle hooks."""

    task: dict[str, Any]
    done: bool | None = None
    event: dict[str, Any] | None = None
    ok: bool = True
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_response(cls, response: dict[str, Any]) -> "ProviderDriverResult":
        task = response.get("task")
        if not isinstance(task, dict):
            raise ValueError("Provider driver response did not include a task.")
        extra = {
            key: copy.deepcopy(value)
            for key, value in response.items()
            if key not in {"task", "done", "event", "ok"}
        }
        return cls(
            task=copy.deepcopy(task),
            done=response.get("done"),
            event=copy.deepcopy(response.get("event")),
            ok=bool(response.get("ok", True)),
            extra=extra,
        )


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
    uses_lifecycle_driver = False

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
        if self.uses_lifecycle_driver:
            context = ProviderTaskContext(backend=self.backend, request=request)
            result = self.start_invocation(context)
            return self._create_response_from_driver_result(result)
        return self._create(request)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(f"{self.backend} provider does not implement create.")

    def send(self, request: dict[str, Any]) -> dict[str, Any]:
        return self.create(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        if self.uses_lifecycle_driver:
            task = self.task_from_request_or_store(task_id, request)
            context = ProviderTaskContext(
                backend=self.backend,
                request=request,
                task_id=task_id,
                task=task,
                metadata=copy.deepcopy(task.get("metadata") or {}),
            )
            result = self.inspect_invocation(context)
            return self._poll_response_from_driver_result(result)
        return self._poll(task_id, request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError(f"{self.backend} provider does not implement poll.")

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        """Start a provider invocation and return a normalized driver result."""

        return ProviderDriverResult.from_response(self._create(context.request))

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        """Inspect one provider invocation without blocking for completion."""

        if not context.task_id:
            raise ValueError("Provider poll context did not include a task id.")
        return ProviderDriverResult.from_response(self._poll(context.task_id, context.request))

    def _create_response_from_driver_result(
        self,
        result: ProviderDriverResult | dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self._normalize_driver_result(result)
        self.persist_task_snapshot(normalized.task)
        return {
            "task": copy.deepcopy(normalized.task),
            **copy.deepcopy(normalized.extra),
        }

    def _poll_response_from_driver_result(
        self,
        result: ProviderDriverResult | dict[str, Any],
    ) -> dict[str, Any]:
        normalized = self._normalize_driver_result(result)
        self.persist_task_snapshot(normalized.task)
        done = normalized.done
        if done is None:
            done = self.task_is_terminal(normalized.task)
        return {
            "done": bool(done),
            "task": copy.deepcopy(normalized.task),
            "event": copy.deepcopy(normalized.event),
            **copy.deepcopy(normalized.extra),
        }

    def _normalize_driver_result(
        self,
        result: ProviderDriverResult | dict[str, Any],
    ) -> ProviderDriverResult:
        if isinstance(result, ProviderDriverResult):
            return result
        if isinstance(result, dict):
            return ProviderDriverResult.from_response(result)
        raise TypeError(f"{self.backend} provider returned an invalid driver result: {type(result)!r}")

    def task_is_terminal(self, task: dict[str, Any]) -> bool:
        status = task.get("status") if isinstance(task.get("status"), dict) else {}
        return str(status.get("state") or "") in TERMINAL_TASK_STATES

    def persist_task_snapshot(self, task: dict[str, Any]) -> None:
        from maos_runtime.a2a_task_store import store_idempotent_task

        store_idempotent_task(copy.deepcopy(task))

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
            "task": copy.deepcopy(task),
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
