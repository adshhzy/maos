import unittest

from maos_runtime.sandbox.api_projection import (
    _normalize_task_status,
    _task_detail_for_api,
    _task_from_state,
    _task_list_item_for_api,
)
from maos_runtime.sandbox.constants import API_RESULT_TEXT_LIMIT


class SandboxApiProjectionTests(unittest.TestCase):
    def test_numeric_temporal_status_is_normalized(self) -> None:
        self.assertEqual(_normalize_task_status("1"), "running")
        self.assertEqual(_normalize_task_status("2"), "completed")
        self.assertEqual(_normalize_task_status("4"), "cancelled")
        self.assertEqual(_normalize_task_status("5"), "terminated")
        self.assertEqual(_normalize_task_status("7"), "timed_out")

    def test_task_state_is_slimmed_for_web_api(self) -> None:
        long_text = "x" * (API_RESULT_TEXT_LIMIT + 500)

        task = _task_from_state(
            "task-1",
            {
                "graph_id": "graph-1",
                "graph_name": "Graph 1",
                "nodes": [{"id": "node-1", "summary": long_text}],
                "instance_results": {
                    "node-1": {
                        "latest_comment": long_text,
                        "comments": [{"text": long_text}, {"text": "short"}],
                    }
                },
            },
            status="running",
        )

        node_summary = task["state"]["nodes"][0]["summary"]
        latest_comment = task["state"]["instance_results"]["node-1"]["latest_comment"]
        comment_text = task["state"]["instance_results"]["node-1"]["comments"][0]["text"]

        self.assertLess(len(node_summary), len(long_text))
        self.assertLess(len(latest_comment), len(long_text))
        self.assertLess(len(comment_text), len(long_text))
        self.assertTrue(node_summary.endswith("...<truncated>"))
        self.assertTrue(latest_comment.endswith("...<truncated>"))
        self.assertTrue(comment_text.endswith("...<truncated>"))

    def test_task_list_item_removes_large_node_outputs(self) -> None:
        task = _task_from_state(
            "task-1",
            {
                "graph_id": "graph-1",
                "graph_name": "Graph 1",
                "nodes": [{"id": "node-1", "status": "completed"}],
                "results": {"node-1": {"latest_comment": "large"}},
                "instance_results": {"node-1#1": {"latest_comment": "large"}},
            },
            status="completed",
            result={
                "status": "completed",
                "results": {"node-1": {"latest_comment": "large"}},
                "instance_results": {"node-1#1": {"latest_comment": "large"}},
                "state": {
                    "results": {"node-1": {"latest_comment": "large"}},
                    "instance_results": {"node-1#1": {"latest_comment": "large"}},
                },
            },
        )

        item = _task_list_item_for_api(task)

        self.assertNotIn("results", item["state"])
        self.assertNotIn("instance_results", item["state"])
        self.assertNotIn("results", item["result"])
        self.assertNotIn("instance_results", item["result"])
        self.assertNotIn("results", item["result"]["state"])
        self.assertNotIn("instance_results", item["result"]["state"])

    def test_task_detail_projection_slims_outputs_but_keeps_runtime_metadata(self) -> None:
        long_text = "x" * (API_RESULT_TEXT_LIMIT + 500)
        task = {
            "task_id": "task-detail",
            "workflow_id": "task-detail",
            "graph_id": "graph-detail",
            "graph_name": "Detail Graph",
            "status": "completed",
            "state": {
                "nodes": [
                    {
                        "id": "final_integrator",
                        "status": "completed",
                        "backend": "claude",
                        "claude_task_id": "a2a-claude-detail",
                        "claude_prompt": long_text,
                        "summary": long_text,
                    }
                ],
                "edges": [],
                "results": {
                    "final_integrator": {
                        "agent_backend": "claude",
                        "claude_task_id": "a2a-claude-detail",
                        "latest_comment": long_text,
                        "raw": long_text,
                    }
                },
                "instance_results": {
                    "final_integrator#1": {
                        "agent_backend": "claude",
                        "claude_task_id": "a2a-claude-detail",
                        "stdout": long_text,
                        "history": long_text,
                    }
                },
            },
            "result": {
                "status": "completed",
                "results": {
                    "final_integrator": {
                        "agent_backend": "claude",
                        "claude_task_id": "a2a-claude-detail",
                        "latest_comment": long_text,
                    }
                },
            },
        }

        projected = _task_detail_for_api(task)
        node = projected["state"]["nodes"][0]
        state_result = projected["state"]["results"]["final_integrator"]
        instance_result = projected["state"]["instance_results"]["final_integrator#1"]
        result_payload = projected["result"]["results"]["final_integrator"]

        self.assertEqual(projected["_projection"]["name"], "task_detail")
        self.assertNotIn("claude_prompt", node)
        self.assertTrue(node["claude_prompt_available"])
        self.assertEqual(node["claude_task_id"], "a2a-claude-detail")
        self.assertTrue(node["summary"].endswith("...<truncated>"))
        self.assertTrue(state_result["latest_comment"].endswith("...<truncated>"))
        self.assertNotIn("raw", state_result)
        self.assertTrue(instance_result["stdout"].endswith("...<truncated>"))
        self.assertNotIn("history", instance_result)
        self.assertTrue(result_payload["latest_comment"].endswith("...<truncated>"))

    def test_claude_runtime_metadata_is_preserved_without_prompt_body(self) -> None:
        task = _task_from_state(
            "task-claude",
            {
                "graph_id": "graph-claude",
                "graph_name": "Claude Graph",
                "nodes": [
                    {
                        "id": "node-claude",
                        "backend": "claude",
                        "claude_prompt": "Explain the release plan.",
                        "claude_task_id": "a2a-claude-1",
                        "claude_command": "claude --print --model glm-5.1 <prompt>",
                        "claude_workdir": "D:\\dev\\MAOS\\temporal_execution_core",
                    }
                ],
                "instance_results": {
                    "node-claude#1": {
                        "agent_backend": "claude",
                        "claude_task_id": "a2a-claude-1",
                        "claude_command": "claude --print --model glm-5.1 <prompt>",
                        "claude_workdir": "D:\\dev\\MAOS\\temporal_execution_core",
                        "stdout": "done",
                        "trace": [{"type": "claude.complete"}],
                    }
                },
            },
            status="running",
        )

        node = task["state"]["nodes"][0]
        result = task["state"]["instance_results"]["node-claude#1"]

        self.assertNotIn("claude_prompt", node)
        self.assertTrue(node["claude_prompt_available"])
        self.assertEqual(node["claude_prompt_chars"], len("Explain the release plan."))
        self.assertEqual(node["claude_command"], "claude --print --model glm-5.1 <prompt>")
        self.assertEqual(result["claude_task_id"], "a2a-claude-1")
        self.assertEqual(result["claude_workdir"], "D:\\dev\\MAOS\\temporal_execution_core")
        self.assertEqual(result["trace"], [{"type": "claude.complete"}])


if __name__ == "__main__":
    unittest.main()
