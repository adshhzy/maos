"""Built-in Agent runtime providers."""

from typing import Any

from maos_runtime.a2a_constants import (
    CLAUDE_BACKEND,
    CLAUDE_HUAWEI_BACKEND,
    CODEX_BACKEND,
    EVALUATOR_BACKEND,
    HERMES_BACKEND,
    MULTICA_BACKEND,
    SIMULATOR_BACKEND,
)
from maos_runtime.a2a_provider_base import (
    ProviderDriverResult,
    ProviderRuntime,
    ProviderTaskContext,
)


class SimulatorProvider(ProviderRuntime):
    """Provider used for tests and local simulator-backed nodes."""

    backend = SIMULATOR_BACKEND
    supports_cancel = True
    supports_resume = True
    supports_push_callbacks = True

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _simulator_agent_card

        return _simulator_agent_card(node)

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.simulator_backend import _send_simulator_message

        return ProviderDriverResult.from_response(_send_simulator_message(context.request))

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.simulator_backend import _poll_simulator_task

        if not context.task_id:
            raise ValueError("Simulator provider poll requires a task id.")
        a2a_task = _poll_simulator_task(context.task_id, context.request.get("workflow_id"))
        return ProviderDriverResult(
            task=a2a_task,
            done=a2a_task["status"]["state"] in {"TASK_STATE_COMPLETED", "TASK_STATE_FAILED"},
            event=None,
        )

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

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.multica_backend import _send_multica_message

        return ProviderDriverResult.from_response(_send_multica_message(context.request))

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.multica_backend import _poll_multica_task

        if not context.task_id:
            raise ValueError("Multica provider poll requires a task id.")
        return ProviderDriverResult.from_response(_poll_multica_task(context.task_id))

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

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.hermes_backend import _send_hermes_message

        return ProviderDriverResult.from_response(_send_hermes_message(context.request))

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.hermes_backend import _poll_hermes_task

        if not context.task_id:
            raise ValueError("Hermes provider poll requires a task id.")
        return ProviderDriverResult.from_response(_poll_hermes_task(context.task_id))

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

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.codex_backend import _send_codex_message

        return ProviderDriverResult.from_response(_send_codex_message(context.request))

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.codex_backend import _poll_codex_task

        if not context.task_id:
            raise ValueError("Codex CLI provider poll requires a task id.")
        return ProviderDriverResult.from_response(_poll_codex_task(context.task_id))

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

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.claude_backend import _send_claude_message

        return ProviderDriverResult.from_response(_send_claude_message(context.request))

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.claude_backend import _poll_claude_task

        if not context.task_id:
            raise ValueError("Claude CLI provider poll requires a task id.")
        return ProviderDriverResult.from_response(_poll_claude_task(context.task_id))

    def _cancel(self, task_id: str, request: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.claude_backend import _cancel_claude_task

        return _cancel_claude_task(task_id, request)


class ClaudeHuaweiCliProvider(ClaudeCliProvider):
    """Provider for local Claude CLI routed to Huawei Cloud DeepSeek."""

    backend = CLAUDE_HUAWEI_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _claude_huawei_agent_card

        return _claude_huawei_agent_card(node)

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.claude_backend import _send_claude_message

        return ProviderDriverResult.from_response(
            _send_claude_message(context.request, backend=CLAUDE_HUAWEI_BACKEND)
        )


class EvaluatorProvider(ProviderRuntime):
    """Provider for deterministic local benchmark evaluation."""

    backend = EVALUATOR_BACKEND

    def agent_card(self, node: dict[str, Any]) -> dict[str, Any]:
        from maos_runtime.a2a.cards import _evaluator_agent_card

        return _evaluator_agent_card(node)

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.evaluator_backend import _send_evaluator_message

        return ProviderDriverResult.from_response(_send_evaluator_message(context.request))

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        from maos_runtime.a2a.evaluator_backend import _poll_evaluator_task

        if not context.task_id:
            raise ValueError("Evaluator provider poll requires a task id.")
        return ProviderDriverResult.from_response(_poll_evaluator_task(context.task_id))
