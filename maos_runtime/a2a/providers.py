"""Built-in Agent runtime providers.

The provider objects are intentionally thin. They route A2A-shaped requests to
the backend-specific functions in ``runtime`` while keeping registration and
backend selection outside the core request handlers.
"""

import copy
from typing import Any

from maos_runtime.a2a_constants import CLAUDE_BACKEND, CODEX_BACKEND, HERMES_BACKEND, MULTICA_BACKEND, SIMULATOR_BACKEND
from maos_runtime.a2a_provider_base import AgentRuntimeProvider
from maos_runtime.a2a.agent_service_client import _timestamp
from maos_runtime.a2a_task_store import TASKS as _TASKS, TASKS_LOCK as _TASKS_LOCK
from maos_runtime.http_json_client import request_json as _request_json
from maos_runtime.runtime_config import agent_service_api_base as _agent_service_api_base


class SimulatorProvider(AgentRuntimeProvider):
    """Provider used for tests and local simulator-backed nodes."""

    backend = SIMULATOR_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _simulator_agent_card

        return _simulator_agent_card(node)

    def capabilities(self) -> dict[str, Any]:
        return _capabilities(self.backend, cancel=True, resume=True, push_callbacks=True)

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.simulator_backend import _send_simulator_message

        return _send_simulator_message(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.simulator_backend import _poll_simulator_task

        a2a_task = _poll_simulator_task(task_id, request.get("workflow_id"))
        return {
            "done": a2a_task["status"]["state"] in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"},
            "task": a2a_task,
            "event": None,
        }

    def cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return _cancel_local_a2a_task(task_id, request)

    def resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        from maos_runtime.a2a.simulator_backend import _resume_simulator_task

        return _resume_simulator_task(task_id, request)


class MulticaProvider(AgentRuntimeProvider):
    """Provider for real Multica-backed Agent tasks."""

    backend = MULTICA_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _multica_agent_card

        return _multica_agent_card(node)

    def capabilities(self) -> dict[str, Any]:
        return _capabilities(self.backend, cancel=True, resume=True, push_callbacks=False)

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.multica_backend import _send_multica_message

        return _send_multica_message(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.multica_backend import _poll_multica_task

        return _poll_multica_task(task_id)

    def cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return _cancel_agent_service_a2a_task(task_id, request)

    def resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        from maos_runtime.a2a.multica_backend import _resume_multica_task

        return _resume_multica_task(task_id, request)


class HermesOneshotProvider(AgentRuntimeProvider):
    """Provider for direct one-shot Hermes runtime calls."""

    backend = HERMES_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _hermes_agent_card

        return _hermes_agent_card(node)

    def capabilities(self) -> dict[str, Any]:
        return _capabilities(self.backend, cancel=True, resume=True, push_callbacks=False)

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.hermes_backend import _send_hermes_message

        return _send_hermes_message(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.hermes_backend import _poll_hermes_task

        return _poll_hermes_task(task_id)

    def cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return _cancel_agent_service_a2a_task(task_id, request)

    def resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        from maos_runtime.a2a.hermes_backend import _resume_hermes_task

        return _resume_hermes_task(task_id, request)


class CodexCliProvider(AgentRuntimeProvider):
    """Provider for direct local Codex CLI one-shot calls."""

    backend = CODEX_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _codex_agent_card

        return _codex_agent_card(node)

    def capabilities(self) -> dict[str, Any]:
        return _capabilities(self.backend, cancel=True, resume=False, push_callbacks=False)

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.codex_backend import _send_codex_message

        return _send_codex_message(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.codex_backend import _poll_codex_task

        return _poll_codex_task(task_id)

    def cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.codex_backend import _cancel_codex_task

        return _cancel_codex_task(task_id, request)

    def resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("Codex CLI provider does not support human-input resume yet.")


class ClaudeCliProvider(AgentRuntimeProvider):
    """Provider for direct local Claude CLI one-shot calls."""

    backend = CLAUDE_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _claude_agent_card

        return _claude_agent_card(node)

    def capabilities(self) -> dict[str, Any]:
        return _capabilities(self.backend, cancel=True, resume=False, push_callbacks=False)

    def create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.claude_backend import _send_claude_message

        return _send_claude_message(request)

    def poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.claude_backend import _poll_claude_task

        return _poll_claude_task(task_id)

    def cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.claude_backend import _cancel_claude_task

        return _cancel_claude_task(task_id, request)

    def resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        raise NotImplementedError("Claude CLI provider does not support human-input resume yet.")


def _capabilities(
    backend: str,
    *,
    cancel: bool,
    resume: bool,
    push_callbacks: bool,
) -> dict[str, Any]:
    return {
        "api_version": "agent-provider-v1",
        "backend": backend,
        "operations": {
            "create": True,
            "poll": True,
            "cancel": cancel,
            "resume": resume,
            "events": True,
            "artifacts": True,
            "push_callbacks": push_callbacks,
        },
    }


def _cancel_local_a2a_task(task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
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


def _cancel_agent_service_a2a_task(task_id: str, request: dict[str, Any]) -> dict[str, Any]:
    with _TASKS_LOCK:
        record = _TASKS[task_id]
        task = record["task"]
        metadata = task["metadata"]
        agent_task_id = metadata["agentServiceTaskId"]
    projection = _request_json(
        _agent_service_api_base(),
        "POST",
        f"/api/v1/agent-tasks/{agent_task_id}/cancel",
        {
            "reason": request.get("reason"),
            "requested_by": request.get("requested_by", "maos"),
        },
    )
    local = _cancel_local_a2a_task(task_id, request)
    local["agent_task"] = projection
    return local
