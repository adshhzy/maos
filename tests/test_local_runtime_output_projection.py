import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from maos_runtime.sandbox.task_archive import archive_task
from web.web_agent_api import build_local_runtime_output


class LocalRuntimeOutputProjectionTests(unittest.TestCase):
    def test_reads_full_claude_result_file(self) -> None:
        long_output = "# 完整报告\n\n" + ("这一段不能被截断。" * 2000)
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-test" / "stdout.json"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    json.dumps({"type": "result", "result": long_output}, ensure_ascii=False),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-test", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertTrue(result["ok"])
        self.assertEqual(result["output_chars"], len(long_output))
        self.assertEqual(result["final_output"]["content"], long_output)
        self.assertNotIn("...<truncated>", result["final_output"]["content"])

    def test_reads_claude_stream_json_output_and_trace(self) -> None:
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-stream" / "stdout.jsonl"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    "\n".join(
                        [
                            json.dumps({"type": "system", "session_id": "s1"}),
                            json.dumps(
                                {
                                    "type": "assistant",
                                    "message": {
                                        "content": [
                                            {"type": "tool_use", "name": "Bash", "input": {"command": "echo ok"}},
                                            {"type": "text", "text": "working"},
                                        ]
                                    },
                                }
                            ),
                            json.dumps({"type": "result", "subtype": "success", "result": "# Stream final"}),
                        ]
                    ),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-stream", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertTrue(result["ok"])
        self.assertEqual(result["final_output"]["content"], "# Stream final")
        self.assertTrue(result["trace"])
        self.assertTrue(any(step.get("tool") == "Bash" for step in result["trace"]))

    def test_claude_stream_text_chunks_are_merged_for_trace(self) -> None:
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-chunks" / "stdout.jsonl"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    "\n".join(
                        [
                            json.dumps({"type": "system", "session_id": "s1"}),
                            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "第一段"}]}}),
                            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "，第二段"}]}}),
                            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "，第三段"}]}}),
                            json.dumps({"type": "result", "subtype": "success", "result": "完整最终输出"}),
                        ]
                    ),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-chunks", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        text_steps = [step for step in result["trace"] if step.get("type") == "claude.text"]
        self.assertEqual(len(text_steps), 1)
        self.assertEqual(text_steps[0]["preview"], "第一段，第二段，第三段")
        self.assertEqual(text_steps[0]["chunk_count"], 3)
        self.assertFalse(text_steps[0]["preview"].strip().startswith("{"))

    def test_claude_nested_stream_events_are_aggregated_for_trace(self) -> None:
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-nested" / "stdout.jsonl"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    "\n".join(
                        [
                            json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
                            json.dumps({"type": "system", "subtype": "thinking_tokens", "estimated_tokens": 1}),
                            json.dumps({"type": "stream_event", "event": {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "先分析"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "再判断"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "最终"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "回答"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 1}}),
                            json.dumps({"type": "result", "subtype": "success", "result": "最终回答"}),
                        ]
                    ),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-nested", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        trace = result["trace"]
        self.assertEqual([step["type"] for step in trace], ["claude.system", "claude.thinking", "claude.text", "claude.result"])
        self.assertEqual(trace[1]["preview"], "先分析再判断")
        self.assertEqual(trace[2]["preview"], "最终回答")
        self.assertTrue(all(step["type"] != "claude.stream_event" for step in trace))

    def test_claude_assistant_replay_is_ignored_when_stream_events_exist(self) -> None:
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-dedupe" / "stdout.jsonl"
                output_file.parent.mkdir(parents=True)
                tool_part = {"type": "tool_use", "name": "Bash", "input": {"command": "echo ok"}}
                output_file.write_text(
                    "\n".join(
                        [
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": tool_part}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
                            json.dumps({"type": "assistant", "message": {"content": [tool_part]}}),
                            json.dumps({"type": "result", "subtype": "success", "result": "done"}),
                        ]
                    ),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-dedupe", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        tool_steps = [step for step in result["trace"] if step.get("type") == "claude.tool_use"]
        self.assertEqual(len(tool_steps), 1)
        self.assertIn("echo ok", tool_steps[0]["preview"])

    def test_claude_input_json_delta_is_parsed_for_tool_preview(self) -> None:
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-input-json" / "stdout.jsonl"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    "\n".join(
                        [
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "name": "Bash"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"command\":\"echo"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": " ok\"}"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
                            json.dumps({"type": "result", "subtype": "success", "result": "done"}),
                        ]
                    ),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-input-json", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        tool_steps = [step for step in result["trace"] if step.get("type") == "claude.tool_use"]
        self.assertEqual(tool_steps[0]["preview"], '{"command":"echo ok"}')

    def test_running_claude_stream_output_uses_partial_text_not_raw_jsonl(self) -> None:
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-running-text" / "stdout.jsonl"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    "\n".join(
                        [
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "部分"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "输出"}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}}),
                        ]
                    ),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-running-text", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertEqual(result["final_output"]["content"], "部分输出")
        self.assertNotIn('"stream_event"', result["final_output"]["content"])

    def test_running_claude_stream_without_text_does_not_show_raw_jsonl_as_output(self) -> None:
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "claude-provider" / "a2a-task-running-thinking" / "stdout.jsonl"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(
                    "\n".join(
                        [
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}}}),
                            json.dumps({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "分析中"}}}),
                        ]
                    ),
                    encoding="utf-8",
                )

                result = build_local_runtime_output("a2a-task-running-thinking", "claude")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertEqual(result["final_output"]["content"], "")
        self.assertNotIn('"stream_event"', result["final_output"]["content"])

    def test_reads_full_codex_final_output_file(self) -> None:
        long_output = "# Codex 完整报告\n\n" + ("完整内容。" * 2000)
        previous = os.environ.get("MAOS_DATA_DIR")
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                output_file = Path(tmp) / "codex-provider" / "a2a-task-test" / "final_output.md"
                output_file.parent.mkdir(parents=True)
                output_file.write_text(long_output, encoding="utf-8")

                result = build_local_runtime_output("a2a-task-test", "codex")
        finally:
            if previous is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous

        self.assertTrue(result["ok"])
        self.assertEqual(result["output_chars"], len(long_output))
        self.assertEqual(result["final_output"]["content"], long_output)
        self.assertNotIn("...<truncated>", result["final_output"]["content"])

    def test_recovers_evaluator_report_from_archived_workflow_payload(self) -> None:
        previous_data_dir = os.environ.get("MAOS_DATA_DIR")
        previous_store = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
        task_id = "a2a-task-deterministic_evaluator-archive-test"
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = str(Path(tmp) / "provider_tasks.sqlite3")
                with sqlite3.connect(os.environ["A2A_PROVIDER_TASK_DB_FILE"]) as conn:
                    conn.execute(
                        """
                        CREATE TABLE provider_tasks (
                            provider_task_id TEXT PRIMARY KEY,
                            idempotency_key TEXT UNIQUE,
                            workflow_id TEXT,
                            node_id TEXT,
                            backend TEXT,
                            status TEXT,
                            created_at TEXT,
                            updated_at TEXT,
                            finished_at TEXT,
                            result_artifact_created INTEGER NOT NULL DEFAULT 0,
                            task_json TEXT NOT NULL
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO provider_tasks (
                            provider_task_id, workflow_id, node_id, backend, status,
                            created_at, updated_at, result_artifact_created, task_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            "task-evaluator-archive-test",
                            "deterministic_evaluator",
                            "evaluator",
                            "TASK_STATE_COMPLETED",
                            "2026-01-01T00:00:00Z",
                            "2026-01-01T00:00:01Z",
                            1,
                            json.dumps(
                                {
                                    "id": task_id,
                                    "status": {"state": "TASK_STATE_COMPLETED"},
                                    "metadata": {"backend": "evaluator"},
                                },
                                ensure_ascii=False,
                            ),
                        ),
                    )
                archive_task(
                    {
                        "task_id": "task-evaluator-archive-test",
                        "workflow_id": "task-evaluator-archive-test",
                        "graph_id": "evaluate-test",
                        "graph_name": "Evaluator Archive Test",
                        "status": "completed",
                        "state": {
                            "nodes": [
                                {
                                    "id": "deterministic_evaluator",
                                    "backend": "evaluator",
                                    "status": "completed",
                                    "a2a_task_id": task_id,
                                }
                            ],
                            "results": {
                                "deterministic_evaluator": {
                                    "benchmark_id": "async_ttl_cache",
                                    "winner": "multi",
                                    "single_score": 84.44,
                                    "multi_score": 100.0,
                                }
                            },
                        },
                        "result": {
                            "results": {
                                "deterministic_evaluator": {
                                    "benchmark_id": "async_ttl_cache",
                                    "winner": "multi",
                                    "single_score": 84.44,
                                    "multi_score": 100.0,
                                }
                            }
                        },
                    }
                )

                result = build_local_runtime_output(task_id, "evaluator")
        finally:
            if previous_data_dir is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous_data_dir
            if previous_store is None:
                os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
            else:
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_store

        self.assertTrue(result["ok"])
        self.assertEqual(result["evaluation_report"]["winner"], "multi")
        self.assertEqual(result["evaluation_report"]["single"]["score"], 84.44)
        self.assertEqual(result["evaluation_report"]["multi"]["score"], 100.0)
        self.assertIn("reconstructed from the archived workflow payload", result["final_output"]["content"])

    def test_reads_evaluator_report_from_execution_store_before_provider_store(self) -> None:
        previous_data_dir = os.environ.get("MAOS_DATA_DIR")
        previous_store = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
        task_id = "a2a-task-deterministic_evaluator-store-test"
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = str(Path(tmp) / "missing_provider_tasks.sqlite3")
                archive_task(
                    {
                        "task_id": "task-evaluator-store-test",
                        "workflow_id": "task-evaluator-store-test",
                        "graph_id": "evaluate-store-test",
                        "graph_name": "Evaluator Store Test",
                        "status": "completed",
                        "state": {
                            "nodes": [
                                {
                                    "id": "deterministic_evaluator",
                                    "backend": "evaluator",
                                    "status": "completed",
                                    "a2a_task_id": task_id,
                                }
                            ],
                            "results": {
                                "deterministic_evaluator": {
                                    "benchmark_id": "async_ttl_cache_hard_concurrency",
                                    "evaluation_task_id": task_id,
                                    "winner": "multi",
                                    "single": {
                                        "score": 50,
                                        "hidden_test_cases": [
                                            {"name": "test_expired_get_removes_key", "status": "failed"},
                                        ],
                                    },
                                    "multi": {
                                        "score": 100,
                                        "hidden_test_cases": [
                                            {"name": "test_expired_get_removes_key", "status": "passed"},
                                        ],
                                    },
                                    "markdown": "# Stored evaluator report",
                                }
                            },
                        },
                        "result": {
                            "results": {
                                "deterministic_evaluator": {
                                    "benchmark_id": "async_ttl_cache_hard_concurrency",
                                    "evaluation_task_id": task_id,
                                    "winner": "multi",
                                    "single": {
                                        "score": 50,
                                        "hidden_test_cases": [
                                            {"name": "test_expired_get_removes_key", "status": "failed"},
                                        ],
                                    },
                                    "multi": {
                                        "score": 100,
                                        "hidden_test_cases": [
                                            {"name": "test_expired_get_removes_key", "status": "passed"},
                                        ],
                                    },
                                    "markdown": "# Stored evaluator report",
                                }
                            }
                        },
                    }
                )

                result = build_local_runtime_output(task_id, "evaluator")
        finally:
            if previous_data_dir is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous_data_dir
            if previous_store is None:
                os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
            else:
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_store

        self.assertTrue(result["ok"])
        self.assertTrue(result["evaluation_report"]["execution_store_source"])
        self.assertTrue(result["output_file"].endswith("execution_store.sqlite3"))
        self.assertIn("Stored evaluator report", result["final_output"]["content"])
        matrix = result["evaluation_report"]["test_matrix"]
        self.assertEqual(matrix["rows"][0]["statuses"]["test_expired_get_removes_key"], "failed")
        self.assertEqual(matrix["rows"][1]["statuses"]["test_expired_get_removes_key"], "passed")

    def test_evaluator_report_backfills_hidden_test_matrix_from_legacy_stdout(self) -> None:
        previous_data_dir = os.environ.get("MAOS_DATA_DIR")
        previous_store = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
        previous_recover = os.environ.get("MAOS_EVALUATOR_RECOVER_UNKNOWN_CASES")
        task_id = "a2a-task-deterministic_evaluator-matrix-backfill"
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
                os.environ["MAOS_DATA_DIR"] = tmp
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = str(Path(tmp) / "provider_tasks.sqlite3")
                os.environ["MAOS_EVALUATOR_RECOVER_UNKNOWN_CASES"] = "0"
                report = {
                    "status": "completed",
                    "benchmark_id": "async_ttl_cache_hard_concurrency",
                    "winner": "multi",
                    "single": {
                        "score": 85,
                        "hidden_tests": {"passed": 11, "failed": 3, "errors": 0, "skipped": 0},
                        "commands": [
                            {
                                "name": "hidden_pytest",
                                "passed": False,
                                "exit_code": 1,
                                "stdout": "..F..F...F.... [100%]\n3 failed, 11 passed in 0.5s",
                                "stderr": "",
                            }
                        ],
                    },
                    "multi": {
                        "score": 100,
                        "hidden_tests": {"passed": 14, "failed": 0, "errors": 0, "skipped": 0},
                        "commands": [
                            {
                                "name": "hidden_pytest",
                                "passed": True,
                                "exit_code": 0,
                                "stdout": ".............. [100%]\n14 passed in 0.5s",
                                "stderr": "",
                            }
                        ],
                    },
                    "markdown": "# Legacy report",
                }
                archive_task(
                    {
                        "task_id": "task-evaluator-matrix-backfill",
                        "workflow_id": "task-evaluator-matrix-backfill",
                        "graph_id": "evaluate-test",
                        "graph_name": "Evaluator Matrix Backfill Test",
                        "status": "completed",
                        "state": {
                            "nodes": [
                                {
                                    "id": "deterministic_evaluator",
                                    "backend": "evaluator",
                                    "status": "completed",
                                    "a2a_task_id": task_id,
                                }
                            ],
                            "results": {"deterministic_evaluator": {"report": report}},
                        },
                        "result": {"results": {"deterministic_evaluator": {"report": report}}},
                    }
                )

                result = build_local_runtime_output(task_id, "evaluator")
        finally:
            if previous_data_dir is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous_data_dir
            if previous_store is None:
                os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
            else:
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_store
            if previous_recover is None:
                os.environ.pop("MAOS_EVALUATOR_RECOVER_UNKNOWN_CASES", None)
            else:
                os.environ["MAOS_EVALUATOR_RECOVER_UNKNOWN_CASES"] = previous_recover

        matrix = result["evaluation_report"]["test_matrix"]
        self.assertEqual(len(matrix["tests"]), 14)
        single_statuses = matrix["rows"][0]["statuses"]
        multi_statuses = matrix["rows"][1]["statuses"]
        self.assertEqual(list(single_statuses.values()).count("failed"), 3)
        self.assertEqual(list(single_statuses.values()).count("passed"), 11)
        self.assertEqual(list(multi_statuses.values()).count("passed"), 14)

    def test_reads_evaluator_report_from_legacy_runtime_db(self) -> None:
        previous_data_dir = os.environ.get("MAOS_DATA_DIR")
        previous_legacy_dir = os.environ.get("MAOS_LEGACY_DATA_DIR")
        previous_store = os.environ.get("A2A_PROVIDER_TASK_DB_FILE")
        task_id = "a2a-task-deterministic_evaluator-legacy-db-test"
        report = {
            "benchmark_id": "async_ttl_cache",
            "winner": "multi",
            "score_delta": 20,
            "single": {"score": 80, "hidden_tests": {"passed": 8, "failed": 2, "errors": 0}},
            "multi": {"score": 100, "hidden_tests": {"passed": 10, "failed": 0, "errors": 0}},
            "markdown": "# Full evaluator report",
        }
        task = {
            "id": task_id,
            "status": {"state": "TASK_STATE_COMPLETED"},
            "metadata": {"backend": "evaluator", "nodeId": "deterministic_evaluator"},
            "artifacts": [
                {
                    "name": "dag-node-result",
                    "parts": [{"data": {"report": report, "agent_backend": "evaluator"}}],
                }
            ],
        }
        try:
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as current, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as legacy:
                os.environ["MAOS_DATA_DIR"] = current
                os.environ["MAOS_LEGACY_DATA_DIR"] = legacy
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = str(Path(current) / "provider_tasks.sqlite3")
                db_file = Path(legacy) / "maos_runtime.db"
                with sqlite3.connect(db_file) as conn:
                    conn.execute(
                        """
                        CREATE TABLE provider_tasks (
                            provider_task_id TEXT PRIMARY KEY,
                            idempotency_key TEXT UNIQUE,
                            workflow_id TEXT,
                            node_id TEXT,
                            backend TEXT,
                            status TEXT,
                            created_at TEXT,
                            updated_at TEXT,
                            finished_at TEXT,
                            result_artifact_created INTEGER NOT NULL DEFAULT 0,
                            task_json TEXT NOT NULL
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO provider_tasks (
                            provider_task_id, node_id, backend, status, created_at,
                            updated_at, result_artifact_created, task_json
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            task_id,
                            "deterministic_evaluator",
                            "evaluator",
                            "TASK_STATE_COMPLETED",
                            "2026-01-01T00:00:00Z",
                            "2026-01-01T00:00:01Z",
                            1,
                            json.dumps(task, ensure_ascii=False),
                        ),
                    )

                result = build_local_runtime_output(task_id, "evaluator")
        finally:
            if previous_data_dir is None:
                os.environ.pop("MAOS_DATA_DIR", None)
            else:
                os.environ["MAOS_DATA_DIR"] = previous_data_dir
            if previous_legacy_dir is None:
                os.environ.pop("MAOS_LEGACY_DATA_DIR", None)
            else:
                os.environ["MAOS_LEGACY_DATA_DIR"] = previous_legacy_dir
            if previous_store is None:
                os.environ.pop("A2A_PROVIDER_TASK_DB_FILE", None)
            else:
                os.environ["A2A_PROVIDER_TASK_DB_FILE"] = previous_store

        self.assertTrue(result["ok"])
        self.assertFalse(result["evaluation_report"].get("archived_fallback", False))
        self.assertEqual(result["evaluation_report"]["multi"]["hidden_tests"]["passed"], 10)
        self.assertIn("Full evaluator report", result["final_output"]["content"])


if __name__ == "__main__":
    unittest.main()
