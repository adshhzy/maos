import unittest

from maos_runtime.sandbox.api_projection import _task_from_state, _task_list_item_for_api
from maos_runtime.sandbox.constants import API_RESULT_TEXT_LIMIT


class SandboxApiProjectionTests(unittest.TestCase):
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
