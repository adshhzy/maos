import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from maos_runtime.a2a import poll_task, provider_capabilities, send_message
from maos_runtime.a2a_task_store import TASKS, TASKS_LOCK
from maos_runtime.evaluation.extraction import extract_python_files
from maos_runtime.evaluation.runner import _output_from_task, _pytest_summary, _run_hidden_pytest, run_evaluation
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

COMPLETE_CORE_OUTPUT = """
## maos_cache.py
```python
class CacheInfo:
    pass

class AsyncTTLCache:
    pass

def ttl_cache(maxsize=128, ttl=60):
    pass

def async_ttl_cache(maxsize=128, ttl=60):
    pass
```
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

    def test_final_integrator_falls_back_to_complete_core_output(self) -> None:
        task = {
            "task_id": "task-multi-example",
            "state": {
                "nodes": [
                    {"id": "core_implementation", "status": "completed", "backend": "simulator"},
                    {"id": "final_integrator", "status": "completed", "backend": "simulator"},
                ],
            },
            "result": {
                "results": {
                    "core_implementation": {"latest_comment": COMPLETE_CORE_OUTPUT},
                    "final_integrator": {"latest_comment": MULTI_OUTPUT},
                },
            },
        }

        output = _output_from_task(
            task,
            node_id="final_integrator",
            api_base="http://sandbox.example",
        )

        self.assertIn("def ttl_cache", output)
        self.assertIn("def async_ttl_cache", output)

    @patch("maos_runtime.evaluation.runner.request_json")
    def test_runner_prefers_full_provider_artifact_over_truncated_local_output(
        self,
        mock_request_json,
    ) -> None:
        runtime_id = "a2a-task-single_coding_agent-full-artifact"
        with TASKS_LOCK:
            TASKS[runtime_id] = {
                "task": {
                    "id": runtime_id,
                    "artifacts": [
                        {
                            "parts": [
                                {
                                    "data": {
                                        "payload": {
                                            "latest_comment": COMPLETE_CORE_OUTPUT,
                                        }
                                    }
                                }
                            ]
                        }
                    ],
                },
                "result_artifact_created": True,
            }
        task = {
            "task_id": "task-single-example",
            "state": {
                "nodes": [
                    {
                        "id": "single_coding_agent",
                        "status": "completed",
                        "backend": "claude",
                        "claude_task_id": runtime_id,
                    },
                ],
            },
            "result": {
                "results": {
                    "single_coding_agent": {
                        "latest_comment": "```python\nclass AsyncTTLCache:\n    pass\n...<truncated>",
                    },
                },
            },
        }
        mock_request_json.return_value = {
            "final_output": {
                "content": "```python\nclass AsyncTTLCache:\n    pass\n...<truncated>",
            }
        }

        output = _output_from_task(
            task,
            node_id="single_coding_agent",
            api_base="http://sandbox.example",
        )

        self.assertIn("def ttl_cache", output)
        self.assertNotIn("...<truncated>", output)

    @patch("maos_runtime.evaluation.runner.request_json")
    def test_runner_reads_claude_huawei_local_runtime_output(
        self,
        mock_request_json,
    ) -> None:
        task = {
            "task_id": "task-multi-huawei",
            "state": {
                "nodes": [
                    {
                        "id": "final_integrator",
                        "status": "completed",
                        "backend": "claude-huawei",
                        "a2a_task_id": "a2a-task-final-huawei",
                        "claude_task_id": "a2a-task-final-huawei",
                    },
                ],
            },
            "result": {
                "results": {
                    "final_integrator": {
                        "latest_comment": "truncated handoff without maos_cache.py"
                    },
                },
            },
        }
        mock_request_json.return_value = {
            "final_output": {"content": COMPLETE_CORE_OUTPUT},
        }

        output = _output_from_task(
            task,
            node_id="final_integrator",
            api_base="http://sandbox.example",
        )

        self.assertIn("class AsyncTTLCache", output)
        self.assertIn("def ttl_cache", output)
        mock_request_json.assert_called_once()
        self.assertEqual(
            mock_request_json.call_args.kwargs["params"]["backend"],
            "claude-huawei",
        )

    @patch("maos_runtime.evaluation.runner.run_command")
    def test_runner_scores_direct_outputs_without_llm(self, mock_run_command) -> None:
        mock_run_command.return_value = CommandResult(
            name="mock",
            command=["python", "-m", "pytest"],
            exit_code=0,
            stdout="99 passed in 0.01s",
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
        self.assertIn("test_matrix", report)
        self.assertIn("hidden_test_cases", report["single"])
        self.assertIn("hidden_test_cases", report["multi"])
        self.assertTrue(report["test_matrix"]["tests"])
        first_test = report["test_matrix"]["tests"][0]["name"]
        self.assertEqual(report["test_matrix"]["rows"][0]["statuses"][first_test], "passed")
        self.assertEqual(report["test_matrix"]["rows"][1]["statuses"][first_test], "passed")
        self.assertIn("## Hidden Test Matrix", report["markdown"])

    @patch("maos_runtime.evaluation.runner.run_command")
    def test_hidden_pytest_timeout_isolated_to_single_test(
        self,
        mock_run_command,
    ) -> None:
        mock_run_command.side_effect = [
            CommandResult(
                name="hidden_pytest",
                command=["python", "-m", "pytest"],
                exit_code=124,
                stdout=".",
                stderr="Command timed out after 90 seconds",
                duration_seconds=90,
            ),
            CommandResult(
                name="hidden_pytest::test_fast",
                command=["python", "-m", "pytest", "hidden_tests.py::test_fast"],
                exit_code=0,
                stdout="1 passed in 0.01s",
                stderr="",
                duration_seconds=0.01,
            ),
            CommandResult(
                name="hidden_pytest::test_hangs",
                command=["python", "-m", "pytest", "hidden_tests.py::test_hangs"],
                exit_code=124,
                stdout="",
                stderr="Command timed out after 8 seconds",
                duration_seconds=8,
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            (workspace / "hidden_tests.py").write_text(
                "def test_fast():\n    pass\n\n"
                "def test_hangs():\n    pass\n",
                encoding="utf-8",
            )

            result = _run_hidden_pytest(workspace)

        self.assertEqual(result.exit_code, 1)
        summary = _pytest_summary(result)
        self.assertEqual(summary["passed"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertIn("FAILED hidden_tests.py::test_hangs - timed out", result.stdout)
        self.assertIn("MAOS_PYTEST_CASE name=test_fast status=passed", result.stdout)
        self.assertIn("MAOS_PYTEST_CASE name=test_hangs status=timeout", result.stdout)

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
