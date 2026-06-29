import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maos_runtime.a2a import poll_task, provider_capabilities, send_message
from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK
from maos_runtime.evaluation.extraction import extract_python_files
from maos_runtime.evaluation.runner import run_evaluation
from maos_runtime.evaluation.sandbox import CommandResult


SINGLE_OUTPUT = """
## maos_cache.py
```python
class AsyncTTLCache:
    pass
```
"""

MULTI_OUTPUT = """
## maos_cache.py
```python
class AsyncTTLCache:
    pass
```

## 设计说明
这里包含设计说明和使用示例。
"""


class EvaluatorProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        with TASKS_LOCK:
            TASKS.clear()

    def test_extract_python_files_from_markdown(self) -> None:
        extracted = extract_python_files(
            MULTI_OUTPUT,
            required_files=("maos_cache.py",),
        )

        self.assertTrue(extracted.ok)
        self.assertIn("class AsyncTTLCache", extracted.files["maos_cache.py"])

    @patch("maos_runtime.evaluation.runner.run_command")
    def test_runner_scores_direct_outputs_without_llm(self, mock_run_command) -> None:
        mock_run_command.return_value = CommandResult(
            name="mock",
            command=["python", "-m", "pytest"],
            exit_code=0,
            stdout="8 passed in 0.01s",
            stderr="",
            duration_seconds=0.01,
        )
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MAOS_EVALUATION_TMPDIR": tmp}):
                report = run_evaluation(
                    {
                        "benchmark_id": "async_ttl_cache",
                        "single_task_id": "task-single-example",
                        "single_node_id": "single_coding_agent",
                        "multi_task_id": "task-multi-example",
                        "multi_node_id": "final_integrator",
                        "single_output": SINGLE_OUTPUT,
                        "multi_output": MULTI_OUTPUT,
                    }
                )

        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["winner"], "multi")
        self.assertGreater(report["multi"]["score"], report["single"]["score"])
        command_names = [call.args[0] for call in mock_run_command.call_args_list]
        self.assertNotIn("agent_pytest", command_names)
        self.assertNotIn("own_tests", report["single"])
        self.assertNotIn("own_tests", report["multi"])
        self.assertEqual(report["rubric"]["hidden_tests"], 70)
        self.assertNotIn("own_tests", report["rubric"])
        self.assertEqual(report["comparison_sources"]["single"]["task_id"], "task-single-example")
        self.assertEqual(report["comparison_sources"]["single"]["node_id"], "single_coding_agent")
        self.assertEqual(report["comparison_sources"]["multi"]["task_id"], "task-multi-example")
        self.assertEqual(report["comparison_sources"]["multi"]["node_id"], "final_integrator")
        self.assertIn("deterministic evaluator provider", report["markdown"])

    @patch("maos_runtime.evaluation.runner.request_json")
    @patch("maos_runtime.evaluation.runner.run_command")
    def test_runner_resolves_latest_completed_tasks_from_selectors(
        self,
        mock_run_command,
        mock_request_json,
    ) -> None:
        mock_run_command.return_value = CommandResult(
            name="mock",
            command=["python", "-m", "pytest"],
            exit_code=0,
            stdout="8 passed in 0.01s",
            stderr="",
            duration_seconds=0.01,
        )

        def task_row(task_id: str, graph_id: str, node_id: str, updated_at: float) -> dict:
            return {
                "task_id": task_id,
                "workflow_id": task_id,
                "graph_id": graph_id,
                "graph_name": graph_id,
                "status": "completed",
                "updated_at": updated_at,
                "state": {
                    "nodes": [
                        {
                            "id": node_id,
                            "status": "completed",
                            "backend": "simulator",
                        }
                    ]
                },
            }

        task_details = {
            "task-single-old": {
                **task_row("task-single-old", "single-graph", "single_coding_agent", 10),
                "result": {"results": {"single_coding_agent": {"latest_comment": "old"}}},
            },
            "task-single-new": {
                **task_row("task-single-new", "single-graph", "single_coding_agent", 20),
                "result": {"results": {"single_coding_agent": {"latest_comment": SINGLE_OUTPUT}}},
            },
            "task-multi-old": {
                **task_row("task-multi-old", "multi-graph", "final_integrator", 15),
                "result": {"results": {"final_integrator": {"latest_comment": "old"}}},
            },
            "task-multi-new": {
                **task_row("task-multi-new", "multi-graph", "final_integrator", 30),
                "result": {"results": {"final_integrator": {"latest_comment": MULTI_OUTPUT}}},
            },
        }

        def fake_request_json(api_base, method, path, params=None):
            if path == "/api/tasks":
                return {"tasks": list(task_details.values())}
            task_id = path.rsplit("/", 1)[-1]
            return task_details[task_id]

        mock_request_json.side_effect = fake_request_json
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict("os.environ", {"MAOS_EVALUATION_TMPDIR": tmp}):
                report = run_evaluation(
                    {
                        "benchmark_id": "async_ttl_cache",
                        "single_selector": {
                            "graph_id": "single-graph",
                            "node_id": "single_coding_agent",
                        },
                        "multi_selector": {
                            "graph_id": "multi-graph",
                            "node_id": "final_integrator",
                        },
                    }
                )

        self.assertEqual(report["comparison_sources"]["single"]["task_id"], "task-single-new")
        self.assertEqual(report["comparison_sources"]["single"]["node_id"], "single_coding_agent")
        self.assertTrue(report["comparison_sources"]["single"]["resolved_from_selector"])
        self.assertEqual(report["comparison_sources"]["multi"]["task_id"], "task-multi-new")
        self.assertEqual(report["comparison_sources"]["multi"]["node_id"], "final_integrator")
        self.assertTrue(report["comparison_sources"]["multi"]["resolved_from_selector"])
        self.assertEqual(report["winner"], "multi")

    def test_evaluator_provider_completes_a2a_task(self) -> None:
        message = {
            "messageId": "test-message",
            "contextId": "test-context",
            "role": "ROLE_USER",
            "parts": [
                {
                    "mediaType": "application/json",
                    "data": {
                        "node": {
                            "id": "deterministic_evaluator",
                            "operation": "evaluate",
                            "agent": {"backend": "evaluator"},
                            "params": {
                                "benchmark_id": "async_ttl_cache",
                                "single_output": SINGLE_OUTPUT,
                                "multi_output": MULTI_OUTPUT,
                            },
                        },
                        "dependency_artifacts": [],
                        "graph_input": {},
                    },
                }
            ],
        }
        self.assertIn("evaluator", provider_capabilities()["providers"])

        with patch("maos_runtime.evaluation.runner.run_command") as mock_run_command:
            mock_run_command.return_value = CommandResult(
                name="mock",
                command=["python", "-m", "pytest"],
                exit_code=0,
                stdout="8 passed in 0.01s",
                stderr="",
                duration_seconds=0.01,
            )
            created = send_message(
                {
                    "message": message,
                    "metadata": {
                        "workflow_id": "test-workflow",
                        "node_id": "deterministic_evaluator",
                        "idempotency_key": "eval-test-key",
                    },
                }
            )["task"]
            polled = poll_task({"id": created["id"], "task": created})

        self.assertTrue(polled["done"])
        task = polled["task"]
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        result = task["artifacts"][0]["parts"][0]["data"]
        self.assertEqual(result["payload"]["winner"], "multi")
        self.assertIn("report", json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
