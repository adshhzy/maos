import unittest
import os
import tempfile

from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK
from maos_runtime.workflows.activities import _dependency_artifacts_for_executions


class ActivityDependencyArtifactTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_data_dir = os.environ.get("MAOS_DATA_DIR")
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["MAOS_DATA_DIR"] = self._tmp.name

    def tearDown(self) -> None:
        with TASKS_LOCK:
            TASKS.clear()
        self._tmp.cleanup()
        if self._previous_data_dir is None:
            os.environ.pop("MAOS_DATA_DIR", None)
        else:
            os.environ["MAOS_DATA_DIR"] = self._previous_data_dir

    def test_stores_full_artifact_and_sends_ref_for_downstream_input(self) -> None:
        full_text = "完整上游输出" * 5000
        compact_text = full_text[:100] + "...<truncated>"
        task_id = "a2a-upstream-full"
        full_artifact = {
            "artifactId": "artifact-full",
            "name": "dag-node-result",
            "parts": [
                {
                    "data": {
                        "node": "upstream",
                        "operation": "agent_task",
                        "payload": {"latest_comment": full_text},
                    },
                    "mediaType": "application/json",
                }
            ],
            "metadata": {"nodeId": "upstream"},
        }
        compact_artifact = {
            **full_artifact,
            "parts": [
                {
                    "data": {
                        "node": "upstream",
                        "operation": "agent_task",
                        "payload": {"latest_comment": compact_text},
                    },
                    "mediaType": "application/json",
                }
            ],
        }
        with TASKS_LOCK:
            TASKS[task_id] = {
                "task": {"id": task_id, "artifacts": [full_artifact], "metadata": {}},
                "result_artifact_created": True,
            }

        artifacts = _dependency_artifacts_for_executions(
            {"upstream": {"a2a_task": {"id": task_id, "artifacts": [compact_artifact]}}}
        )

        payload = artifacts[0]["parts"][0]["data"]["payload"]
        self.assertIn("artifact_ref", payload)
        self.assertIn("uri", payload)
        self.assertNotIn("...<truncated>", payload["summary"])

    def test_inline_mode_loads_full_artifact_from_task_store(self) -> None:
        full_text = "完整上游输出" * 5000
        task_id = "a2a-upstream-inline"
        full_artifact = {
            "artifactId": "artifact-full",
            "name": "dag-node-result",
            "parts": [
                {
                    "data": {
                        "node": "upstream",
                        "operation": "agent_task",
                        "payload": {"latest_comment": full_text},
                    },
                    "mediaType": "application/json",
                }
            ],
            "metadata": {"nodeId": "upstream"},
        }
        with TASKS_LOCK:
            TASKS[task_id] = {
                "task": {"id": task_id, "artifacts": [full_artifact], "metadata": {}},
                "result_artifact_created": True,
            }

        artifacts = _dependency_artifacts_for_executions(
            {"upstream": {"a2a_task": {"id": task_id, "artifacts": []}}},
            transfer_mode="inline",
        )

        payload = artifacts[0]["parts"][0]["data"]["payload"]
        self.assertEqual(payload["summary"], full_text)
        self.assertNotIn("artifact_ref", payload)

    def test_falls_back_to_compact_artifact_when_task_store_is_unavailable(self) -> None:
        compact_text = "compact...<truncated>"
        artifacts = _dependency_artifacts_for_executions(
            {
                "upstream": {
                    "a2a_task": {
                        "id": "missing-task",
                        "artifacts": [
                            {
                                "artifactId": "artifact-compact",
                                "name": "dag-node-result",
                                "parts": [
                                    {
                                        "data": {
                                            "node": "upstream",
                                            "operation": "agent_task",
                                            "payload": {"latest_comment": compact_text},
                                        },
                                        "mediaType": "application/json",
                                    }
                                ],
                            }
                        ],
                    }
                }
            }
        )

        payload = artifacts[0]["parts"][0]["data"]["payload"]
        self.assertEqual(payload["summary"], compact_text)


if __name__ == "__main__":
    unittest.main()
