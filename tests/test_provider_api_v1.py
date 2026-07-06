import unittest
from unittest.mock import patch

from maos_runtime.a2a import (
    ProviderDriverResult,
    ProviderRuntime,
    ProviderTaskContext,
    provider_capabilities,
    task_artifacts,
    task_events,
)
from maos_runtime.a2a.providers import (
    ClaudeCliProvider,
    ClaudeHuaweiCliProvider,
    CodexCliProvider,
    EvaluatorProvider,
    HermesOneshotProvider,
    MulticaProvider,
    SimulatorProvider,
)
from maos_runtime.a2a_constants import CLAUDE_HUAWEI_BACKEND
from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK
from services.agent_service.main import _agent_service_capabilities


class ProviderApiV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        with TASKS_LOCK:
            TASKS.clear()

    def test_provider_capabilities_are_discoverable(self) -> None:
        capabilities = provider_capabilities()

        self.assertEqual(capabilities["api_version"], "agent-provider-v1")
        self.assertIn("simulator", capabilities["providers"])
        self.assertIn("multica", capabilities["providers"])
        self.assertIn("hermes", capabilities["providers"])
        self.assertIn("codex", capabilities["providers"])
        self.assertIn("evaluator", capabilities["providers"])
        self.assertTrue(capabilities["providers"]["simulator"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["simulator"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["simulator"]["operations"]["artifacts"])
        self.assertTrue(capabilities["providers"]["multica"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["multica"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["multica"]["operations"]["resume"])
        self.assertTrue(capabilities["providers"]["hermes"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["hermes"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["hermes"]["operations"]["resume"])
        self.assertTrue(capabilities["providers"]["codex"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["codex"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["codex"]["operations"]["cancel"])
        self.assertTrue(capabilities["providers"]["evaluator"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["evaluator"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["evaluator"]["operations"]["artifacts"])

    def test_builtin_providers_share_provider_runtime_template(self) -> None:
        simulator = SimulatorProvider()
        multica = MulticaProvider()
        hermes = HermesOneshotProvider()
        codex = CodexCliProvider()
        claude = ClaudeCliProvider()
        claude_huawei = ClaudeHuaweiCliProvider()
        evaluator = EvaluatorProvider()

        self.assertIsInstance(simulator, ProviderRuntime)
        self.assertIsInstance(multica, ProviderRuntime)
        self.assertIsInstance(hermes, ProviderRuntime)
        self.assertIsInstance(codex, ProviderRuntime)
        self.assertIsInstance(claude, ProviderRuntime)
        self.assertIsInstance(claude_huawei, ProviderRuntime)
        self.assertIsInstance(evaluator, ProviderRuntime)
        self.assertTrue(simulator.capabilities()["operations"]["resume"])
        self.assertTrue(multica.capabilities()["operations"]["resume"])
        self.assertTrue(hermes.capabilities()["operations"]["resume"])
        self.assertTrue(codex.capabilities()["operations"]["cancel"])
        self.assertTrue(claude.capabilities()["operations"]["cancel"])
        self.assertFalse(claude.capabilities()["operations"]["resume"])
        self.assertTrue(claude_huawei.capabilities()["operations"]["cancel"])
        self.assertFalse(evaluator.capabilities()["operations"]["cancel"])
        with self.assertRaisesRegex(NotImplementedError, "does not support human-input resume"):
            claude.resume("a2a-task-test", {})

    def test_multica_provider_uses_lifecycle_template(self) -> None:
        provider = MulticaProvider()
        task = _task("a2a-task-multica", "multica", "TASK_STATE_WORKING")
        completed = _task("a2a-task-multica", "multica", "TASK_STATE_COMPLETED")

        with patch(
            "maos_runtime.a2a.multica_backend._send_multica_message",
            return_value={"task": task},
        ) as send, patch(
            "maos_runtime.a2a.multica_backend._poll_multica_task",
            return_value={"done": True, "task": completed, "event": {"type": "completed"}},
        ) as poll:
            created = provider.create({"message": {"role": "ROLE_USER"}})
            polled = provider.poll(
                "a2a-task-multica",
                {"id": "a2a-task-multica", "task": created["task"]},
            )

        send.assert_called_once()
        poll.assert_called_once_with("a2a-task-multica")
        self.assertTrue(polled["done"])
        self.assertEqual(polled["task"]["status"]["state"], "TASK_STATE_COMPLETED")

    def test_hermes_provider_uses_lifecycle_template(self) -> None:
        provider = HermesOneshotProvider()
        task = _task("a2a-task-hermes", "hermes", "TASK_STATE_WORKING")
        completed = _task("a2a-task-hermes", "hermes", "TASK_STATE_COMPLETED")

        with patch(
            "maos_runtime.a2a.hermes_backend._send_hermes_message",
            return_value={"task": task},
        ) as send, patch(
            "maos_runtime.a2a.hermes_backend._poll_hermes_task",
            return_value={"done": True, "task": completed, "event": {"type": "completed"}},
        ) as poll:
            created = provider.create({"message": {"role": "ROLE_USER"}})
            polled = provider.poll(
                "a2a-task-hermes",
                {"id": "a2a-task-hermes", "task": created["task"]},
            )

        send.assert_called_once()
        poll.assert_called_once_with("a2a-task-hermes")
        self.assertTrue(polled["done"])
        self.assertEqual(polled["task"]["status"]["state"], "TASK_STATE_COMPLETED")

    def test_codex_cli_provider_uses_lifecycle_template(self) -> None:
        provider = CodexCliProvider()
        task = _task("a2a-task-codex", "codex", "TASK_STATE_WORKING")
        completed = _task("a2a-task-codex", "codex", "TASK_STATE_COMPLETED")

        with patch(
            "maos_runtime.a2a.codex_backend._send_codex_message",
            return_value={"task": task},
        ) as send, patch(
            "maos_runtime.a2a.codex_backend._poll_codex_task",
            return_value={"done": True, "task": completed, "event": {"type": "completed"}},
        ) as poll:
            created = provider.create({"message": {"role": "ROLE_USER"}})
            polled = provider.poll(
                "a2a-task-codex",
                {"id": "a2a-task-codex", "task": created["task"]},
            )

        send.assert_called_once()
        poll.assert_called_once_with("a2a-task-codex")
        self.assertEqual(created["task"]["id"], "a2a-task-codex")
        self.assertTrue(polled["done"])
        self.assertEqual(polled["task"]["status"]["state"], "TASK_STATE_COMPLETED")

    def test_claude_cli_provider_uses_lifecycle_template(self) -> None:
        provider = ClaudeCliProvider()
        task = _task("a2a-task-claude", "claude", "TASK_STATE_WORKING")
        completed = _task("a2a-task-claude", "claude", "TASK_STATE_COMPLETED")

        with patch(
            "maos_runtime.a2a.claude_backend._send_claude_message",
            return_value={"task": task},
        ) as send, patch(
            "maos_runtime.a2a.claude_backend._poll_claude_task",
            return_value={"done": True, "task": completed, "event": {"type": "completed"}},
        ) as poll:
            created = provider.create({"message": {"role": "ROLE_USER"}})
            polled = provider.poll(
                "a2a-task-claude",
                {"id": "a2a-task-claude", "task": created["task"]},
            )

        send.assert_called_once()
        poll.assert_called_once_with("a2a-task-claude")
        self.assertTrue(polled["done"])
        self.assertEqual(polled["task"]["status"]["state"], "TASK_STATE_COMPLETED")

    def test_claude_huawei_cli_provider_uses_lifecycle_template(self) -> None:
        provider = ClaudeHuaweiCliProvider()
        task = _task("a2a-task-claude-huawei", "claude-huawei", "TASK_STATE_WORKING")

        with patch(
            "maos_runtime.a2a.claude_backend._send_claude_message",
            return_value={"task": task},
        ) as send:
            created = provider.create({"message": {"role": "ROLE_USER"}})

        send.assert_called_once_with(
            {"message": {"role": "ROLE_USER"}},
            backend=CLAUDE_HUAWEI_BACKEND,
        )
        self.assertEqual(created["task"]["metadata"]["backend"], "claude-huawei")

    def test_provider_runtime_template_normalizes_create_and_poll(self) -> None:
        provider = _TemplateProvider()
        created = provider.create({"metadata": {"idempotency_key": "unit-key"}})

        self.assertEqual(created["task"]["id"], "a2a-task-template")
        self.assertTrue(provider.started)

        polled = provider.poll(created["task"]["id"], {"id": created["task"]["id"], "task": created["task"]})

        self.assertTrue(polled["done"])
        self.assertEqual(polled["task"]["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(polled["event"]["type"], "completed")
        self.assertTrue(provider.inspected)
        with TASKS_LOCK:
            self.assertEqual(
                TASKS[created["task"]["id"]]["task"]["status"]["state"],
                "TASK_STATE_COMPLETED",
            )

    def test_provider_events_and_artifacts_use_local_a2a_task_shape(self) -> None:
        task = {
            "id": "a2a-task-test",
            "status": {"state": "TASK_STATE_COMPLETED"},
            "artifacts": [{"artifactId": "artifact-1", "name": "dag-node-result"}],
            "metadata": {"backend": "simulator"},
        }
        with TASKS_LOCK:
            TASKS[task["id"]] = {"task": task, "result_artifact_created": True}

        events = task_events({"id": task["id"]})
        artifacts = task_artifacts({"id": task["id"]})

        self.assertEqual(events["task_id"], task["id"])
        self.assertEqual(events["events"][0]["type"], "task_snapshot")
        self.assertEqual(artifacts["artifacts"][0]["artifactId"], "artifact-1")

    def test_agent_service_v1_capabilities_endpoint(self) -> None:
        body = _agent_service_capabilities()

        self.assertEqual(body["api_version"], "agent-service-v1")
        self.assertTrue(body["operations"]["create"])
        self.assertIn("completed", body["statuses"])
        self.assertEqual(body["endpoints"]["create"], "POST /api/v1/agent-tasks")


def _task(task_id: str, backend: str, state: str) -> dict:
    return {
        "id": task_id,
        "contextId": f"context-{task_id}",
        "status": {"state": state},
        "metadata": {
            "backend": backend,
            "nodeId": f"node-{backend}",
            "workflow_id": "workflow-test",
        },
        "artifacts": [],
    }


class _TemplateProvider(ProviderRuntime):
    backend = "template"

    def __init__(self) -> None:
        self.started = False
        self.inspected = False

    def agent_card(self, node: dict) -> dict:
        return {"name": "template"}

    def start_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        self.started = True
        return ProviderDriverResult(
            task={
                "id": "a2a-task-template",
                "contextId": "context-template",
                "status": {"state": "TASK_STATE_WORKING"},
                "metadata": {
                    "backend": self.backend,
                    "idempotencyKey": "unit-key",
                    "nodeId": "template_node",
                    "workflow_id": "workflow-template",
                },
                "artifacts": [],
            }
        )

    def inspect_invocation(self, context: ProviderTaskContext) -> ProviderDriverResult:
        self.inspected = True
        task = dict(context.task or {})
        task["status"] = {"state": "TASK_STATE_COMPLETED"}
        return ProviderDriverResult(
            task=task,
            event={"type": "completed", "task_id": task["id"]},
        )

    def persist_task_snapshot(self, task: dict) -> None:
        with TASKS_LOCK:
            TASKS[task["id"]] = {"task": dict(task), "result_artifact_created": bool(task.get("artifacts"))}


if __name__ == "__main__":
    unittest.main()
