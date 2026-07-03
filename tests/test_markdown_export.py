import json
from pathlib import Path
from unittest.mock import patch

from web.markdown_export import export_task_markdown


def test_export_task_markdown_reads_claude_provider_full_result(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    runtime_id = "a2a-task-final-report-123"
    run_dir = data_dir / "claude-provider" / runtime_id
    run_dir.mkdir(parents=True)
    full_markdown = "# Final Report\n\nThis is the full provider result."
    (run_dir / "stdout.json").write_text(
        json.dumps({"result": full_markdown}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("MAOS_DATA_DIR", str(data_dir))

    task = {
        "task_id": "task-demo",
        "workflow_id": "task-demo",
        "graph_name": "Demo Graph",
        "state": {
            "nodes": [
                {
                    "id": "final_report",
                    "label": "Final Report",
                    "status": "completed",
                    "backend": "claude",
                    "a2a_task_id": runtime_id,
                    "claude_task_id": runtime_id,
                    "output_available": True,
                }
            ],
            "edges": [],
        },
        "result": {
            "results": {
                "final_report": {
                    "agent_backend": "claude",
                    "claude_task_id": runtime_id,
                    "latest_comment": "truncated",
                }
            }
        },
    }

    result = export_task_markdown(
        task,
        output_dir=str(tmp_path / "exports"),
        node_id="final_report",
    )

    output_path = result["output_path"]
    exported = Path(output_path).read_text(encoding="utf-8")
    assert result["content_chars"] == len(full_markdown)
    assert "This is the full provider result." in exported
    assert "truncated" not in exported


def test_export_task_markdown_renders_rich_evaluator_report(tmp_path):
    runtime_id = "a2a-task-deterministic_evaluator-123"
    task = {
        "task_id": "task-eval",
        "workflow_id": "task-eval",
        "graph_name": "Evaluator Graph",
        "state": {
            "nodes": [
                {
                    "id": "deterministic_evaluator",
                    "label": "Deterministic Evaluator",
                    "status": "completed",
                    "backend": "evaluator",
                    "a2a_task_id": runtime_id,
                    "output_available": True,
                }
            ],
            "edges": [],
        },
        "result": {"results": {"deterministic_evaluator": {"agent_backend": "evaluator"}}},
    }
    evaluation_report = {
        "status": "completed",
        "benchmark_id": "async_ttl_cache",
        "winner": "multi",
        "score_delta": 15.56,
        "rubric": {"extract": 10, "compile": 10, "hidden_tests": 70, "docs": 10},
        "comparison_sources": {
            "single": {
                "task_id": "task-single-new",
                "node_id": "single_coding_agent",
                "graph_id": "single-agent-coding",
                "resolved_from_selector": True,
            },
            "multi": {
                "task_id": "task-multi-new",
                "node_id": "final_integrator",
                "graph_id": "multi-agent-coding",
                "resolved_from_selector": True,
            },
        },
        "single": {
            "score": 72.22,
            "score_breakdown": {
                "extract": 10,
                "compile": 10,
                "hidden_tests": 42.22,
                "hidden_test_ratio": 0.6032,
                "docs": 10,
            },
            "extract_ok": True,
            "output_chars": 1000,
            "files": {"maos_cache.py": {"lines": 90, "chars": 3000}},
            "extraction_errors": [],
            "hidden_tests": {"passed": 19, "failed": 12, "errors": 0, "skipped": 1},
            "hidden_test_cases": [
                {"name": "test_cache_hit", "display": "cache hit", "status": "passed"},
                {"name": "test_concurrency", "display": "concurrency", "status": "failed"},
            ],
            "commands": [
                {
                    "name": "hidden_pytest",
                    "passed": False,
                    "exit_code": 1,
                    "duration_seconds": 2.3,
                    "command": ["python", "-m", "pytest", "hidden_tests.py", "-q"],
                    "stdout": "12 failed, 19 passed, 1 skipped",
                    "stderr": "AssertionError: cache_info mismatch",
                }
            ],
        },
        "multi": {
            "score": 87.78,
            "score_breakdown": {
                "extract": 10,
                "compile": 10,
                "hidden_tests": 57.78,
                "hidden_test_ratio": 0.8254,
                "docs": 10,
            },
            "extract_ok": True,
            "output_chars": 2000,
            "files": {"maos_cache.py": {"lines": 120, "chars": 4500}},
            "extraction_errors": [],
            "hidden_tests": {"passed": 26, "failed": 5, "errors": 0, "skipped": 1},
            "hidden_test_cases": [
                {"name": "test_cache_hit", "display": "cache hit", "status": "passed"},
                {"name": "test_concurrency", "display": "concurrency", "status": "passed"},
            ],
            "commands": [
                {
                    "name": "hidden_pytest",
                    "passed": False,
                    "exit_code": 1,
                    "duration_seconds": 2.7,
                    "command": ["python", "-m", "pytest", "hidden_tests.py", "-q"],
                    "stdout": "5 failed, 26 passed, 1 skipped",
                    "stderr": "",
                }
            ],
        },
        "markdown": "# Short Report\n\nWinner: multi",
    }
    mocked_output = {
        "ok": True,
        "backend": "evaluator",
        "task_id": runtime_id,
        "output_file": "registry.json",
        "output_chars": 28,
        "final_output": {"content": "# Short Report\n\nWinner: multi"},
        "evaluation_report": evaluation_report,
    }

    with patch("web.markdown_export.build_local_runtime_output", return_value=mocked_output):
        result = export_task_markdown(
            task,
            output_dir=str(tmp_path / "exports"),
            node_id="deterministic_evaluator",
        )

    exported = Path(result["output_path"]).read_text(encoding="utf-8")
    assert "## Score Method" in exported
    assert "## Score Breakdown" in exported
    assert "task-single-new" in exported
    assert "task-multi-new" in exported
    assert "## Hidden Test Summary" in exported
    assert "## Hidden Test Matrix" in exported
    assert "| Single Agent | PASS | FAIL |" in exported
    assert "| Multi Agent | PASS | PASS |" in exported
    assert "12 failed, 19 passed, 1 skipped" in exported
    assert "stderr:" not in exported
    assert "## Command Details And Failure Reasons" in exported
