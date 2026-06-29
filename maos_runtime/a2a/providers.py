"""Built-in Agent runtime providers."""

from typing import Any

from maos_runtime.a2a_constants import (
    CLAUDE_BACKEND,
    CODEX_BACKEND,
    EVALUATOR_BACKEND,
    HERMES_BACKEND,
    MULTICA_BACKEND,
    SIMULATOR_BACKEND,
)
from maos_runtime.a2a_provider_base import ProviderRuntime


class SimulatorProvider(ProviderRuntime):
    """Provider used for tests and local simulator-backed nodes."""

    backend = SIMULATOR_BACKEND
    supports_cancel = True
    supports_resume = True
    supports_push_callbacks = True

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _simulator_agent_card

        return _simulator_agent_card(node)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.simulator_backend import _send_simulator_message

        return _send_simulator_message(request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.simulator_backend import _poll_simulator_task

        a2a_task = _poll_simulator_task(task_id, request.get("workflow_id"))
        return {
            "done": a2a_task["status"]["state"] in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"},
            "task": a2a_task,
            "event": None,
        }

    def _cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return self.cancel_local_task(task_id, request)

    def _resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        from maos_runtime.a2a.simulator_backend import _resume_simulator_task

        return _resume_simulator_task(task_id, request)


class MulticaProvider(ProviderRuntime):
    """Provider for real Multica-backed Agent tasks."""

    backend = MULTICA_BACKEND
    supports_cancel = True
    supports_resume = True

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _multica_agent_card

        return _multica_agent_card(node)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.multica_backend import _send_multica_message

        return _send_multica_message(request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.multica_backend import _poll_multica_task

        return _poll_multica_task(task_id)

    def _cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return self.cancel_agent_service_task(task_id, request)

    def _resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        from maos_runtime.a2a.multica_backend import _resume_multica_task

        return _resume_multica_task(task_id, request)


class HermesOneshotProvider(ProviderRuntime):
    """Provider for direct one-shot Hermes runtime calls."""

    backend = HERMES_BACKEND
    supports_cancel = True
    supports_resume = True

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _hermes_agent_card

        return _hermes_agent_card(node)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.hermes_backend import _send_hermes_message

        return _send_hermes_message(request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.hermes_backend import _poll_hermes_task

        return _poll_hermes_task(task_id)

    def _cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        return self.cancel_agent_service_task(task_id, request)

    def _resume(
        self,
        task_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        from maos_runtime.a2a.hermes_backend import _resume_hermes_task

        return _resume_hermes_task(task_id, request)


class CodexCliProvider(ProviderRuntime):
    """Provider for direct local Codex CLI one-shot calls."""

    backend = CODEX_BACKEND
    supports_cancel = True

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _codex_agent_card

        return _codex_agent_card(node)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.codex_backend import _send_codex_message

        return _send_codex_message(request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.codex_backend import _poll_codex_task

        return _poll_codex_task(task_id)

    def _cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.codex_backend import _cancel_codex_task

        return _cancel_codex_task(task_id, request)


class ClaudeCliProvider(ProviderRuntime):
    """Provider for direct local Claude CLI one-shot calls."""

    backend = CLAUDE_BACKEND
    supports_cancel = True

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _claude_agent_card

        return _claude_agent_card(node)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.claude_backend import _send_claude_message

        return _send_claude_message(request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.claude_backend import _poll_claude_task

        return _poll_claude_task(task_id)

    def _cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.claude_backend import _cancel_claude_task

        return _cancel_claude_task(task_id, request)


class EvaluatorProvider(ProviderRuntime):
    """Provider for deterministic local benchmark evaluation."""

    backend = EVALUATOR_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _evaluator_agent_card

        return _evaluator_agent_card(node)

    def _create(self, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.evaluator_backend import _send_evaluator_message

        return _send_evaluator_message(request)

    def _poll(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.evaluator_backend import _poll_evaluator_task

        return _poll_evaluator_task(task_id)
