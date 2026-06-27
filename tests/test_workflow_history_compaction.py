import unittest

from maos_runtime.workflows.json_dag import (
    WORKFLOW_HISTORY_TEXT_LIMIT,
    _compact_dependency_executions_for_activity,
    _compact_execution_for_history,
)


class WorkflowHistoryCompactionTests(unittest.TestCase):
    def test_compacts_large_agent_payloads_before_history_storage(self) -> None:
        long_text = "x" * (WORKFLOW_HISTORY_TEXT_LIMIT + 500)
        execution = {
            "status": "completed",
            "instance_id": "node-1#1",
            "result": {
                "node": "node-1",
                "payload": {
                    "latest_comment": long_text,
                    "comments": [{"text": long_text}],
                    "trace": [{"description": long_text}],
                },
            },
            "a2a_task": {
                "id": "a2a-1",
                "status": {"state": "TASK_STATE_COMPLETED"},
                "metadata": {"hermesPrompt": long_text},
                "artifacts": [
                    {
                        "name": "dag-node-result",
                        "parts": [{"data": {"payload": {"stdout": long_text}}}],
                    }
                ],
            },
        }

        compact = _compact_execution_for_history(execution)

        payload = compact["result"]["payload"]
        self.assertLess(len(payload["latest_comment"]), len(long_text))
        self.assertNotIn("comments", payload)
        self.assertNotIn("trace", payload)
        self.assertIn("_omitted_for_workflow_history", payload)
        artifact_stdout = compact["a2a_task"]["artifacts"][0]["parts"][0]["data"]["payload"]["stdout"]
        self.assertLess(len(artifact_stdout), len(long_text))

    def test_compacts_dependency_executions_before_activity_schedule(self) -> None:
        long_text = "x" * (WORKFLOW_HISTORY_TEXT_LIMIT + 500)
        compact = _compact_dependency_executions_for_activity(
            {
                "upstream": {
                    "status": "completed",
                    "instance_id": "upstream#1",
                    "result": {"payload": {"latest_comment": long_text}},
                    "a2a_task": {"id": "a2a-1", "status": {}, "artifacts": []},
                }
            }
        )

        latest_comment = compact["upstream"]["result"]["payload"]["latest_comment"]
        self.assertLess(len(latest_comment), len(long_text))
        self.assertTrue(latest_comment.endswith("...<truncated>"))


if __name__ == "__main__":
    unittest.main()
