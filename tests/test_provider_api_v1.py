import unittest

from maos_runtime.a2a import ProviderRuntime, provider_capabilities, task_artifacts, task_events
from maos_runtime.a2a.providers import ClaudeCliProvider, EvaluatorProvider, SimulatorProvider
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
        self.assertIn("codex", capabilities["providers"])
        self.assertIn("evaluator", capabilities["providers"])
        self.assertTrue(capabilities["providers"]["simulator"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["simulator"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["simulator"]["operations"]["artifacts"])
        self.assertTrue(capabilities["providers"]["codex"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["codex"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["codex"]["operations"]["cancel"])
        self.assertTrue(capabilities["providers"]["evaluator"]["operations"]["create"])
        self.assertTrue(capabilities["providers"]["evaluator"]["operations"]["poll"])
        self.assertTrue(capabilities["providers"]["evaluator"]["operations"]["artifacts"])

    def test_builtin_providers_share_provider_runtime_lifecycle(self) -> None:
        simulator = SimulatorProvider()
        claude = ClaudeCliProvider()
        evaluator = EvaluatorProvider()

        self.assertIsInstance(simulator, ProviderRuntime)
        self.assertTrue(simulator.capabilities()["operations"]["resume"])
        self.assertTrue(claude.capabilities()["operations"]["cancel"])
        self.assertFalse(claude.capabilities()["operations"]["resume"])
        self.assertFalse(evaluator.capabilities()["operations"]["cancel"])
        with self.assertRaisesRegex(NotImplementedError, "does not support human-input resume"):
            claude.resume("a2a-task-test", {})

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


if __name__ == "__main__":
    unittest.main()
